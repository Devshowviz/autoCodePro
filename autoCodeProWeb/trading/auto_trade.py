# trading/auto_trade.py
import time

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

# 매매 루프 주기(초).
# 매수 탐색 시 마켓·시세 조회 2회에 후보 코인당 캔들 조회 1회(최대 5회)까지
# 나가므로, 업비트 시세 API 제한(초당 10회)을 넘지 않도록 여유를 둔다.
TRADE_INTERVAL_SECONDS = 3

RSI_PERIOD = 14           # RSI 기간
RSI_CANDLE_UNIT = 1       # 분봉 단위 (1분봉)
RSI_CANDLE_COUNT = 200    # 조회할 캔들 개수 (Wilder 평활이 수렴하도록 넉넉히)
RSI_BUY_THRESHOLD = 30    # 이 값 이하이면 과매도로 보고 매수

TRAILING_STOP_RATE = 0.99  # 최고점 대비 -1% 하락 시 매도
STOP_LOSS_RATE = 0.97      # 매수가 대비 -3% 하락 시 손절


class AutoTrader:
    def __init__(self, budget):
        """자동매매 트레이더"""
        self.budget = budget
        self.active = False
        self.current_order = None
        self.highest_price = 0  # 트레일링 스탑 최고점

    def log(self, message):
        """ ✅ 로그 저장 및 최대 50개까지만 유지 """
        print(message)
        trade_logs.append(message)
        if len(trade_logs) > 50:
            trade_logs.pop(0)

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

    def start_trading(self):
        """자동매매 시작"""
        self.active = True
        self.log("🚀 자동매매 시작됨!")

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
        if self.current_order is None:
            self.try_buy()
            return

        self.check_sell()

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
        self.highest_price = best_coin["trade_price"]

    def check_sell(self):
        """ ✅ 매도 조건 체크 (트레일링 스탑 / 손절) """
        market = self.current_order["market"]
        buy_price = self.current_order["buy_price"]

        current_price = get_ticker_price(market)
        if current_price is None:
            self.log(f"⚠️ 현재가 조회 실패: {market}")
            return

        # 최고점 갱신
        if current_price > self.highest_price:
            self.highest_price = current_price

        self.log(
            f"📊 현재 가격: {current_price}원 "
            f"(매수가: {buy_price}원, 최고점: {self.highest_price}원)"
        )

        # ✅ 트레일링 스탑: 최고점 대비 하락 시 매도
        if self.highest_price * TRAILING_STOP_RATE >= current_price:
            self.sell_all(market, current_price, "🚀 매도 실행 (트레일링 스탑)")
            return

        # ✅ 손절
        if current_price <= buy_price * STOP_LOSS_RATE:
            self.sell_all(market, current_price, "🛑 손절 매도")

    def sell_all(self, market, current_price, reason):
        """ 보유 수량 전량을 시장가로 매도

        시장가 매도(ord_type="market")는 volume 이 필수이므로,
        계좌에서 실제 보유 수량을 조회해 넘긴다.
        """
        currency = market.split("-")[-1]
        volume = get_balance(currency)

        if volume <= 0:
            self.log(f"⚠️ 매도할 수량이 없음: {market} - 보유 상태를 초기화합니다")
            self.current_order = None
            self.highest_price = 0
            return

        sell_order = upbit_order(market, "sell", volume=volume, ord_type="market")

        if "error" in sell_order:
            self.log(f"❌ 매도 실패: {market} - {sell_order['error']}")
            return

        self.log(f"{reason}: {market}, 가격: {current_price}원, 수량: {volume}")
        self.current_order = None
        self.highest_price = 0
