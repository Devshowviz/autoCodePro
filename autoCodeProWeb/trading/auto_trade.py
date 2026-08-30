# trading/auto_trade.py
import threading
import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone

from .market_analysis import BULLISH, analyze_market_state, select_buy_target
from .models import AskRecord, DailyPnlRecord, FailedMarket, TradeRecord
from .utils import (
    get_account_info,
    get_balance,
    get_held_currencies,
    get_krw_market_coin_info,
    get_ticker_price,
    is_order_done,
    upbit_order,
)

trade_logs = []      # ✅ 자동매매 로그 저장 리스트
profit_logs = []     # 매도 체결 내역 (매수가/매도가/수익률)

# 업비트 기준 시간대. KST 는 서머타임이 없어 고정 오프셋으로 충분하며,
# zoneinfo 와 달리 Windows 에서 tzdata 패키지를 요구하지 않는다.
KST = dt_timezone(timedelta(hours=9))

TRADE_INTERVAL_SECONDS = 1     # 매매 루프 주기(초)
MAX_RETRY = 3                  # 루프 오류 연속 허용 횟수
MAX_POSITIONS = 3              # 동시 보유 종목 수 상한
MIN_KRW_BALANCE = 10000        # 이 금액 미만이면 매수 중단
REBUY_BLOCK_SECONDS = 600      # 매도 후 같은 종목 재매수 차단 시간(10분)
BUY_SETTLE_GRACE_SECONDS = 30  # 매수 직후 체결이 잔고에 반영될 때까지 기다리는 시간

# 버그 4: 주문 실패 종목 차단에 만료 시간을 둔다. 일시적 오류로 영구히
# 매수 대상에서 빠지는 것을 막는다.
FAILED_MARKET_BLOCK_SECONDS = 3600

FEE_RATE = 0.0005              # 업비트 원화마켓 수수료(편도)

# --- §8 매도 전략 -----------------------------------------------------
QUICK_PROFIT_RATE = 0.01           # +1% : 보합/하락장에서 즉시 매도
TRAILING_ACTIVATE_RATE = 0.02      # +2% : 트레일링 스탑 활성화
TRAILING_DROP_RATE = 0.01          # 최고점 대비 -1% 하락 시 매도
STOP_LOSS_RATE = -0.02             # 일반 손절 -2%
VOLATILE_STOP_LOSS_RATE = -0.04    # 고변동성 손절 -4%
VOLATILE_CHANGE_RATE = 0.05        # 변동률 절댓값 5% 이상이면 고변동성
HOLD_SECONDS_BULLISH = 360         # 상승장 보유 5분
HOLD_SECONDS_OTHERWISE = 600       # 그 외 보유 10분

# --- 일일 손실 한도 ---------------------------------------------------
# 당일 누적 실현손익이 매매원금 대비 이 비율에 도달하면 전량 청산 후 정지한다.
DAILY_LOSS_CUT_RATE = -0.10


class AutoTrader:
    def __init__(self, budget):
        """자동매매 트레이더"""
        self.budget = budget
        self.active = False

        # {market: {"buy_price", "highest_price", "uuid", "created_at", "buy_krw_price"}}
        # 매매 스레드와 웹 요청 스레드가 함께 접근하므로 락으로 보호한다
        self.positions = {}
        self.lock = threading.Lock()

        self.market_state = "neutral"
        self.pnl_date = self.today()
        # 재시작하거나 새 트레이더를 만들어도 당일 손실 한도가 유지되도록
        # 당일 누적 손익은 DB 에서 복원한다
        record, _ = DailyPnlRecord.objects.get_or_create(date=self.pnl_date)
        self.daily_pnl = record.realized_pnl

        self.restore_positions()

    # ------------------------------------------------------------------
    # 공통
    # ------------------------------------------------------------------

    def log(self, message):
        """ ✅ 로그 저장 및 최대 50개까지만 유지 """
        print(message)
        trade_logs.append(message)
        if len(trade_logs) > 50:
            trade_logs.pop(0)

    def today(self):
        """ 업비트 기준(KST) 오늘 날짜 """
        return datetime.now(KST).date()

    def daily_loss_limit(self):
        """ 당일 허용 손실 한도(원, 음수) """
        return self.budget * DAILY_LOSS_CUT_RATE

    def get_available_krw(self, accounts=None):
        """ ✅ 현재 사용 가능한 원화(KRW) 잔고 조회 """
        if accounts is None:
            accounts = get_account_info()

        if not isinstance(accounts, list):
            self.log(f"❌ 계좌 조회 실패: {accounts.get('error', accounts)}")
            return 0

        for account in accounts:
            if account.get("currency") == "KRW":
                return float(account.get("balance", 0))
        return 0

    # ------------------------------------------------------------------
    # 포지션 관리 (메모리 + DB 이중 관리)
    # ------------------------------------------------------------------

    def positions_snapshot(self):
        """ 웹 요청 스레드에서 안전하게 읽을 수 있는 보유 목록 사본 """
        with self.lock:
            return list(self.positions)

    def restore_positions(self):
        """ 재시작 시 DB 의 활성 거래를 메모리로 복원 """
        for record in TradeRecord.objects.filter(is_active=True):
            self.positions[record.market] = {
                "buy_price": record.buy_price,
                "highest_price": record.highest_price or record.buy_price,
                "uuid": record.uuid,
                "created_at": record.created_at,
                "buy_krw_price": record.buy_krw_price,
            }
        if self.positions:
            self.log(f"♻️ 이전 보유 종목 {len(self.positions)}개 복원: {', '.join(self.positions)}")

    def open_position(self, market, buy_price, order_uuid, buy_krw_price):
        """ 매수 성공 시 메모리와 DB 에 기록 """
        now = timezone.now()
        record, created = TradeRecord.objects.update_or_create(
            market=market,
            defaults={
                "buy_price": buy_price,
                "highest_price": buy_price,
                "uuid": order_uuid,
                "is_active": True,
                "buy_krw_price": buy_krw_price,
            },
        )
        if not created:
            # auto_now_add 는 최초 삽입 때만 찍히므로, 같은 종목을 다시 사면
            # 예전 매수 시각이 남아 보유 시간 계산이 왜곡된다. 직접 갱신한다.
            TradeRecord.objects.filter(pk=record.pk).update(created_at=now)

        with self.lock:
            self.positions[market] = {
                "buy_price": buy_price,
                "highest_price": buy_price,
                "uuid": order_uuid,
                "created_at": now if not created else record.created_at,
                "buy_krw_price": buy_krw_price,
            }

    def close_position(self, market):
        """ 보유 종료 처리 """
        with self.lock:
            self.positions.pop(market, None)
        TradeRecord.objects.filter(market=market).update(is_active=False)
        AskRecord.objects.update_or_create(
            market=market, defaults={"recorded_at": timezone.now()}
        )

    def update_highest_price(self, market, price):
        """ 최고가 갱신 (메모리·DB 동시) """
        self.positions[market]["highest_price"] = price
        TradeRecord.objects.filter(market=market).update(highest_price=price)

    # ------------------------------------------------------------------
    # 매매 루프
    # ------------------------------------------------------------------

    def start_trading(self):
        """자동매매 시작"""
        self.active = True
        self.log(
            f"🚀 자동매매 시작됨! (매매원금 {self.budget:,}원 / "
            f"일일 손실 한도 {self.daily_loss_limit():,.0f}원 / 최대 {MAX_POSITIONS}종목)"
        )

        failures = 0
        while self.active:
            try:
                self.execute_trade()
                failures = 0
            except Exception as e:
                failures += 1
                self.log(f"❌ 자동매매 처리 중 오류 ({failures}/{MAX_RETRY}): {e}")
                if failures >= MAX_RETRY:
                    self.log("🛑 오류가 반복되어 자동매매를 중단합니다")
                    self.active = False
                    break
            time.sleep(TRADE_INTERVAL_SECONDS)

    def stop_trading(self):
        """자동매매 중지"""
        self.active = False
        self.log("🛑 자동매매 중지됨!")

    def execute_trade(self):
        """자동매매 한 사이클"""
        self.reset_daily_pnl_if_new_day()

        accounts = get_account_info()
        coin_info_list = get_krw_market_coin_info()
        if not coin_info_list:
            self.log("⚠️ 시세 조회 실패 - 이번 사이클 건너뜀")
            return

        self.market_state, _ = analyze_market_state(coin_info_list)
        coin_by_market = {c["market"]: c for c in coin_info_list}

        # 사용자가 업비트 앱에서 직접 매도한 종목 정리
        self.sync_manual_sells(accounts)

        # 보유 종목 매도 판단
        for market in list(self.positions):
            self.check_sell(market, coin_by_market.get(market))

        # 손실 한도 도달 시 전량 청산 후 정지
        if self.check_loss_cut():
            return

        # 신규 매수
        self.try_buy(accounts, coin_info_list)

    def sync_manual_sells(self, accounts):
        """ 사용자가 직접 매도한 종목을 보유 목록에서 제거 """
        held = get_held_currencies(accounts)
        if held is None:
            return  # 계좌 조회 실패 시에는 건드리지 않는다

        now = timezone.now()
        for market in self.positions_snapshot():
            position = self.positions.get(market)
            if position is None:
                continue

            # 시장가 매수 직후에는 체결이 잔고에 반영되기 전이라
            # "보유 없음" 으로 보일 수 있다. 잠시 판정을 유예한다.
            held_seconds = (now - position["created_at"]).total_seconds()
            if held_seconds < BUY_SETTLE_GRACE_SECONDS:
                continue

            if market.split("-")[-1] not in held:
                self.log(f"👤 사용자 매도 감지: {market} - 보유 목록에서 제거합니다")
                self.close_position(market)

    # ------------------------------------------------------------------
    # 일일 손실 관리
    # ------------------------------------------------------------------

    def reset_daily_pnl_if_new_day(self):
        """ 날짜(KST)가 바뀌면 당일 누적 손익을 초기화 """
        today = self.today()
        if today != self.pnl_date:
            self.log(f"📅 날짜 변경 ({self.pnl_date} → {today}) - 당일 손익 초기화")
            self.pnl_date = today
            record, _ = DailyPnlRecord.objects.get_or_create(date=today)
            self.daily_pnl = record.realized_pnl

    def record_trade_result(self, market, buy_price, sell_price, buy_krw_price):
        """ 매도 후 실현손익을 당일 누적치에 반영 (§12 수수료 반영) """
        effective_buy = buy_price * (1 + FEE_RATE)
        effective_sell = sell_price * (1 - FEE_RATE)
        profit_rate = (effective_sell - effective_buy) / effective_buy

        pnl = (buy_krw_price or self.budget) * profit_rate
        self.daily_pnl += pnl
        DailyPnlRecord.objects.update_or_create(
            date=self.pnl_date, defaults={"realized_pnl": self.daily_pnl}
        )

        profit_logs.append({
            "market": market,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit_rate": round(profit_rate * 100, 3),
            "pnl": round(pnl),
            "sold_at": timezone.now().isoformat(),
        })
        if len(profit_logs) > 50:
            profit_logs.pop(0)

        self.log(
            f"💰 {market} 실현손익 {pnl:+,.0f}원 ({profit_rate * 100:+.2f}%) "
            f"| 당일 누적 {self.daily_pnl:+,.0f}원 / 한도 {self.daily_loss_limit():,.0f}원"
        )
        return pnl

    def check_loss_cut(self):
        """ 당일 손실 한도 도달 시 전량 청산 후 정지. 정지했으면 True """
        if self.daily_pnl > self.daily_loss_limit():
            return False

        self.log(
            f"🚨 일일 손실 한도 도달: 당일 누적 {self.daily_pnl:+,.0f}원 "
            f"(한도 {self.daily_loss_limit():,.0f}원)"
        )

        for market in list(self.positions):
            self.sell_all(market, get_ticker_price(market), "🚨 로스컷 청산")

        self.stop_trading()
        return True

    # ------------------------------------------------------------------
    # 매수
    # ------------------------------------------------------------------

    def blocked_markets(self):
        """ 매수 제외 대상: 보유 중 + 주문 실패 이력 + 최근 매도 """
        blocked = set(self.positions_snapshot())

        failed_threshold = timezone.now() - timedelta(seconds=FAILED_MARKET_BLOCK_SECONDS)
        blocked |= set(
            FailedMarket.objects.filter(failed_at__gte=failed_threshold)
            .values_list("market", flat=True)
        )

        threshold = timezone.now() - timedelta(seconds=REBUY_BLOCK_SECONDS)
        blocked |= set(
            AskRecord.objects.filter(recorded_at__gte=threshold)
            .values_list("market", flat=True)
        )
        return blocked

    def try_buy(self, accounts, coin_info_list):
        """ §4 선정 로직으로 종목을 고르고 시장가 매수 """
        if len(self.positions) >= MAX_POSITIONS:
            return

        available_krw = self.get_available_krw(accounts)
        if available_krw < MIN_KRW_BALANCE:
            self.log(f"❌ 잔고 부족: {available_krw:,.0f}원 (최소 {MIN_KRW_BALANCE:,}원)")
            return

        target = select_buy_target(coin_info_list, excluded_markets=self.blocked_markets())
        if target is None:
            return

        market = target["market"]

        # 주문 금액이 잔고를 넘지 않도록 조정 (§3.2).
        # 업비트는 시장가 매수 시 금액 × (1 + 수수료) 를 잠그므로,
        # 잔고 전액을 주문하면 수수료만큼 부족해 거부된다. 헤드룸을 뺀다.
        buy_krw_price = min(self.budget, int(available_krw / (1 + FEE_RATE)))
        if buy_krw_price < MIN_KRW_BALANCE:
            return

        self.log(f"✅ 매수 시도: {market}, 금액: {buy_krw_price:,.0f}원 "
                 f"(변동률 {target['signed_change_rate'] * 100:+.2f}%)")

        buy_order = upbit_order(market, "buy", price=buy_krw_price, ord_type="price")
        if "error" in buy_order:
            self.log(f"❌ 매수 실패: {market} - {buy_order['error']}")
            # 네트워크 오류 같은 일시적 실패는 종목 문제가 아니므로
            # 실패 목록에 올리지 않는다 (다음 사이클에 다시 시도)
            if not buy_order.get("transient"):
                FailedMarket.objects.update_or_create(market=market)
            return

        self.open_position(
            market=market,
            buy_price=target["trade_price"],
            order_uuid=buy_order.get("uuid"),
            buy_krw_price=buy_krw_price,
        )

    # ------------------------------------------------------------------
    # 매도 (§8)
    # ------------------------------------------------------------------

    def check_sell(self, market, coin_info):
        """ 보유 종목 하나에 대해 매도 조건 평가 """
        position = self.positions.get(market)
        if position is None:
            return

        # 이미 조회한 시세를 재사용하고, 목록에 없을 때만 개별 조회한다
        current_price = (coin_info or {}).get("trade_price") or get_ticker_price(market)
        if current_price is None:
            self.log(f"⚠️ 현재가 조회 실패: {market}")
            return

        buy_price = position["buy_price"]
        change_rate = current_price / buy_price - 1

        # 최고가 갱신
        if current_price > position["highest_price"]:
            self.update_highest_price(market, current_price)

        reason = self.decide_sell(market, position, current_price, change_rate, coin_info)
        if reason:
            self.sell_all(market, current_price, reason)

    def decide_sell(self, market, position, current_price, change_rate, coin_info):
        """ 매도 사유를 판단해 문자열로 반환. 매도하지 않으면 None """
        highest_price = position["highest_price"]

        # 8.4 손절 - 고변동성 종목은 더 넓게 잡는다
        volatility = abs((coin_info or {}).get("signed_change_rate") or 0)
        stop_rate = (
            VOLATILE_STOP_LOSS_RATE if volatility >= VOLATILE_CHANGE_RATE else STOP_LOSS_RATE
        )
        if change_rate <= stop_rate:
            label = "고변동성 손절" if stop_rate == VOLATILE_STOP_LOSS_RATE else "손절"
            return f"🛑 {label} ({stop_rate:.0%})"

        # 8.2 트레일링 스탑 - +2% 도달 이후부터 작동
        if highest_price >= position["buy_price"] * (1 + TRAILING_ACTIVATE_RATE):
            if current_price <= highest_price * (1 - TRAILING_DROP_RATE):
                return "🚀 매도 실행 (트레일링 스탑)"
            return None

        # 8.1 수익 실현 - 보합/하락장은 +1% 에서 즉시 정리
        if change_rate >= QUICK_PROFIT_RATE and self.market_state != BULLISH:
            return "🚀 익절 매도 (+1%, 보합/하락장)"

        # 8.3 시간 기반 매도
        hold_limit = (
            HOLD_SECONDS_BULLISH if self.market_state == BULLISH else HOLD_SECONDS_OTHERWISE
        )
        held_seconds = (timezone.now() - position["created_at"]).total_seconds()
        if held_seconds >= hold_limit and change_rate >= QUICK_PROFIT_RATE:
            return f"⏱️ 시간 기반 매도 ({int(hold_limit / 60)}분 경과)"

        return None

    def sell_all(self, market, current_price, reason):
        """ 보유 수량 전량을 시장가로 매도

        시장가 매도(ord_type="market")는 volume 이 필수이므로,
        계좌에서 실제 보유 수량을 조회해 넘긴다.
        """
        position = self.positions.get(market)
        currency = market.split("-")[-1]
        volume = get_balance(currency)

        if volume is None:
            # 계좌 조회가 실패한 것이지 잔고가 없는 것이 아니다.
            # 여기서 포지션을 정리하면 팔지도 않은 코인이 관리 밖으로
            # 사라지므로, 보유 상태를 유지하고 다음 사이클에 다시 시도한다.
            self.log(f"⚠️ 계좌 조회 실패로 매도 보류: {market} - 다음 사이클에 재시도")
            return

        if volume <= 0:
            self.log(f"⚠️ 매도할 수량이 없음: {market} - 보유 상태를 정리합니다")
            self.close_position(market)
            return

        sell_order = upbit_order(market, "sell", volume=volume, ord_type="market")
        if "error" in sell_order:
            self.log(f"❌ 매도 실패: {market} - {sell_order['error']}")
            return

        # 로스컷 청산처럼 호출 시점에 시세 조회가 실패했을 수 있다.
        # 실현손익이 당일 누적에서 빠지지 않도록 한 번 더 조회한다.
        if current_price is None:
            current_price = get_ticker_price(market)

        price_text = f"{current_price:,}원" if current_price is not None else "시장가"
        self.log(f"{reason}: {market}, 가격: {price_text}, 수량: {volume}")

        # §3.3 UUID 로 체결 상태 확인 (미체결이어도 보유 정리는 진행한다)
        if not is_order_done(sell_order.get("uuid")):
            self.log(f"ℹ️ {market} 매도 주문 체결 확인 대기 중")

        if position and current_price:
            self.record_trade_result(
                market, position["buy_price"], current_price, position["buy_krw_price"]
            )
        elif position:
            self.log(f"⚠️ {market} 시세 조회 실패로 실현손익을 기록하지 못했습니다")

        self.close_position(market)
