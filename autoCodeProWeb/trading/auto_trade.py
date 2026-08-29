# trading/auto_trade.py
import time
from datetime import datetime, timedelta, timezone

import requests

from .utils import (
    UPBIT_API_URL,
    REQUEST_TIMEOUT,
    get_account_info,
    get_balance,
    get_krw_market_coin_info,
    get_ticker_price,
    upbit_order,
)

trade_logs = []  # ✅ 자동매매 로그 저장 리스트

# 업비트 기준 시간대. KST 는 서머타임이 없어 고정 오프셋으로 충분하며,
# zoneinfo 와 달리 Windows 에서 tzdata 패키지를 요구하지 않는다.
KST = timezone(timedelta(hours=9))

# 매매 루프 주기(초).
# 매수 탐색 시 마켓·시세 조회 2회에 후보 코인당 캔들 조회 1회(최대 5회)까지
# 나가므로, 업비트 시세 API 제한(초당 10회)을 넘지 않도록 여유를 둔다.
TRADE_INTERVAL_SECONDS = 3

RSI_PERIOD = 14           # RSI 기간
RSI_CANDLE_UNIT = 1       # 분봉 단위 (1분봉)
RSI_CANDLE_COUNT = 200    # 조회할 캔들 개수 (Wilder 평활이 수렴하도록 넉넉히)
RSI_BUY_THRESHOLD = 30    # 이 값 이하이면 과매도로 보고 매수

TAKE_PROFIT_RATE = 0.02   # 매수가 대비 +2% 이면 익절
STOP_LOSS_RATE = -0.02    # 매수가 대비 -2% 이면 손절

# 당일 누적 실현손익이 매매원금(budget) 대비 이 비율에 도달하면 자동매매를 정지한다.
DAILY_LOSS_CUT_RATE = -0.10

# 업비트 원화마켓 거래 수수료(편도). 실현손익 계산 시 왕복으로 차감한다.
FEE_RATE = 0.0005


class AutoTrader:
    def __init__(self, budget):
        """자동매매 트레이더"""
        self.budget = budget
        self.active = False
        self.current_order = None

        # 당일 누적 실현손익(원)과 그 기준 날짜(KST)
        self.daily_pnl = 0.0
        self.pnl_date = self.today()

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

    def get_available_krw(self):
        """ ✅ 현재 사용 가능한 원화(KRW) 잔고 조회 """
        accounts = get_account_info()

        # 오류 응답은 list 가 아닌 dict 로 돌아온다
        if not isinstance(accounts, list):
            self.log(f"❌ 계좌 조회 실패: {accounts.get('error', accounts)}")
            return 0

        for account in accounts:
            if account.get("currency") == "KRW":
                return float(account.get("balance", 0))
        return 0  # KRW 잔고가 없으면 0 반환

    # ------------------------------------------------------------------
    # 지표
    # ------------------------------------------------------------------

    def get_rsi(self, market, period=RSI_PERIOD):
        """ 분봉 데이터로 RSI(Wilder 평활) 계산. 계산 불가하면 None 반환 """
        try:
            response = requests.get(
                f"{UPBIT_API_URL}/v1/candles/minutes/{RSI_CANDLE_UNIT}",
                params={"market": market, "count": RSI_CANDLE_COUNT},
                timeout=REQUEST_TIMEOUT,
            )
            candles = response.json()
        except (requests.RequestException, ValueError) as e:
            self.log(f"⚠️ RSI 캔들 조회 실패: {market} ({e})")
            return None

        if not isinstance(candles, list) or len(candles) < period + 1:
            self.log(f"⚠️ RSI 계산에 필요한 캔들 부족: {market}")
            return None

        # 업비트는 최신 캔들부터 반환하므로 시간 순서대로 뒤집는다
        closes = [candle["trade_price"] for candle in reversed(candles)]
        deltas = [after - before for before, after in zip(closes, closes[1:])]

        gains = [delta if delta > 0 else 0.0 for delta in deltas]
        losses = [-delta if delta < 0 else 0.0 for delta in deltas]

        # 첫 구간은 단순 평균, 이후는 Wilder 평활 적용
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for gain, loss in zip(gains[period:], losses[period:]):
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            # 하락이 전혀 없으면 RSI 는 100 (상승도 없으면 중립 50)
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    # ------------------------------------------------------------------
    # 매매 루프
    # ------------------------------------------------------------------

    def start_trading(self):
        """자동매매 시작"""
        self.active = True
        self.log(
            f"🚀 자동매매 시작됨! (매매원금 {self.budget:,}원 / "
            f"일일 손실 한도 {self.daily_loss_limit():,.0f}원)"
        )

        while self.active:
            # 한 번의 오류로 매매 스레드가 죽지 않도록 감싼다
            try:
                self.execute_trade()
            except Exception as e:
                self.log(f"❌ 자동매매 처리 중 오류: {e}")
            time.sleep(TRADE_INTERVAL_SECONDS)

    def stop_trading(self):
        """자동매매 중지"""
        self.active = False
        self.log("🛑 자동매매 중지됨!")

    def execute_trade(self):
        """자동매매 실행"""
        self.reset_daily_pnl_if_new_day()

        # 손실 한도를 넘었으면 보유분을 청산하고 더 이상 매매하지 않는다
        if self.check_loss_cut():
            return

        if self.current_order is None:
            self.try_buy()
        else:
            self.check_sell()

    # ------------------------------------------------------------------
    # 일일 손실 관리
    # ------------------------------------------------------------------

    def reset_daily_pnl_if_new_day(self):
        """ 날짜(KST)가 바뀌면 당일 누적 손익을 초기화 """
        today = self.today()
        if today != self.pnl_date:
            self.log(f"📅 날짜 변경 ({self.pnl_date} → {today}) - 당일 손익 초기화")
            self.pnl_date = today
            self.daily_pnl = 0.0

    def record_trade_result(self, buy_price, sell_price):
        """ 매도 후 실현손익을 당일 누적치에 반영

        시장가 주문의 실제 체결가는 즉시 알 수 없으므로,
        매수·매도 시점의 시세와 왕복 수수료로 근사한다.
        """
        gross = self.budget * (sell_price / buy_price - 1)
        fee = self.budget * FEE_RATE * 2  # 매수 + 매도
        pnl = gross - fee

        self.daily_pnl += pnl
        self.log(
            f"💰 실현손익 {pnl:+,.0f}원 "
            f"(당일 누적 {self.daily_pnl:+,.0f}원 / 한도 {self.daily_loss_limit():,.0f}원)"
        )
        return pnl

    def check_loss_cut(self):
        """ 당일 손실 한도에 도달했으면 청산 후 정지. 정지했으면 True """
        if self.daily_pnl > self.daily_loss_limit():
            return False

        self.log(
            f"🚨 일일 손실 한도 도달: 당일 누적 {self.daily_pnl:+,.0f}원 "
            f"(한도 {self.daily_loss_limit():,.0f}원)"
        )

        if self.current_order is not None:
            market = self.current_order["market"]
            self.sell_all(market, get_ticker_price(market), "🚨 로스컷 청산")

        self.stop_trading()
        return True

    # ------------------------------------------------------------------
    # 매수 / 매도
    # ------------------------------------------------------------------

    def try_buy(self):
        """ 매수 조건(RSI 과매도)을 만족하는 코인을 찾아 시장가 매수 """
        # ✅ 1. 현재 원화 잔고 확인 (잔고 부족 방지)
        available_krw = self.get_available_krw()
        if available_krw < self.budget:
            self.log(f"❌ 잔고 부족: {available_krw}원 (필요: {self.budget}원)")
            return

        # ✅ 2. RSI 30 이하 종목 찾기
        best_coin = None
        best_rsi = None
        for coin in get_krw_market_coin_info():
            rsi = self.get_rsi(coin["market"])
            if rsi is not None and rsi <= RSI_BUY_THRESHOLD:
                best_coin = coin
                best_rsi = rsi
                break

        if best_coin is None:
            self.log(f"❌ 매수할 적절한 코인이 없음 (RSI {RSI_BUY_THRESHOLD} 이하 조건 미충족)")
            return

        market = best_coin["market"]

        # ✅ 3. 시장가 매수 실행 (ord_type="price" 는 원화 금액을 지정)
        self.log(f"✅ 매수 시도: {market}, 금액: {self.budget}원, RSI: {best_rsi:.2f}")
        buy_order = upbit_order(market, "buy", price=self.budget, ord_type="price")

        if "error" in buy_order:
            self.log(f"❌ 매수 실패: {market} - {buy_order['error']}")
            return

        self.current_order = {
            "market": market,
            "buy_price": best_coin["trade_price"],
        }

    def check_sell(self):
        """ ✅ 매도 조건 체크 (매수가 대비 고정 익절 / 손절) """
        market = self.current_order["market"]
        buy_price = self.current_order["buy_price"]

        current_price = get_ticker_price(market)
        if current_price is None:
            self.log(f"⚠️ 현재가 조회 실패: {market}")
            return

        change_rate = current_price / buy_price - 1
        self.log(
            f"📊 현재 가격: {current_price}원 "
            f"(매수가: {buy_price}원, {change_rate * 100:+.2f}%)"
        )

        if change_rate >= TAKE_PROFIT_RATE:
            self.sell_all(market, current_price, f"🚀 익절 매도 (+{TAKE_PROFIT_RATE:.0%})")
        elif change_rate <= STOP_LOSS_RATE:
            self.sell_all(market, current_price, f"🛑 손절 매도 ({STOP_LOSS_RATE:.0%})")

    def sell_all(self, market, current_price, reason):
        """ 보유 수량 전량을 시장가로 매도

        시장가 매도(ord_type="market")는 volume 이 필수이므로,
        계좌에서 실제 보유 수량을 조회해 넘긴다.
        """
        currency = market.split("-")[-1]
        volume = get_balance(currency)
        buy_price = self.current_order["buy_price"] if self.current_order else None

        if volume <= 0:
            self.log(f"⚠️ 매도할 수량이 없음: {market} - 보유 상태를 초기화합니다")
            self.current_order = None
            return

        sell_order = upbit_order(market, "sell", volume=volume, ord_type="market")

        if "error" in sell_order:
            self.log(f"❌ 매도 실패: {market} - {sell_order['error']}")
            return

        price_text = f"{current_price}원" if current_price is not None else "시장가"
        self.log(f"{reason}: {market}, 가격: {price_text}, 수량: {volume}")
        self.current_order = None

        if buy_price and current_price:
            self.record_trade_result(buy_price, current_price)
