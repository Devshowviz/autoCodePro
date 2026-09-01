# trading/tests.py
from datetime import date, datetime, timedelta
from unittest import mock

import jwt
import pandas as pd
import requests
from django.test import TestCase
from django.utils import timezone

from . import indicators as ind
from . import market_analysis as ma
from . import views
from .auto_trade import AutoTrader, FEE_RATE, MAX_POSITIONS
from .models import AskRecord, DailyPnlRecord, FailedMarket, MarketVolumeRecord, TradeRecord
from .utils import get_ticker_price, upbit_order


def coin(market, change_rate=0.0, price=100.0, volume=1_000_000.0):
    return {
        "market": market,
        "trade_price": price,
        "signed_change_rate": change_rate,
        "acc_trade_price_24h": volume,
    }


def orderbook(market, bid=300.0, ask=100.0, bid_price=100.0, ask_price=100.05):
    return {
        "market": market,
        "total_bid_size": bid,
        "total_ask_size": ask,
        "orderbook_units": [{"ask_price": ask_price, "bid_price": bid_price}],
    }


# ======================================================================
# §6 기술적 지표
# ======================================================================

class IndicatorTests(TestCase):
    def test_RSI_상승만이면_100(self):
        self.assertEqual(ind.calculate_rsi(pd.Series([100.0 + i for i in range(40)])), 100.0)

    def test_RSI_하락만이면_0(self):
        self.assertEqual(ind.calculate_rsi(pd.Series([200.0 - i for i in range(40)])), 0.0)

    def test_RSI_상승과_하락이_같으면_50(self):
        # 15개 -> 델타 14개 (상승 7 / 하락 7)
        self.assertAlmostEqual(ind.calculate_rsi(pd.Series([100.0 + (i % 2) for i in range(15)])), 50.0)

    def test_RSI_데이터_부족하면_None(self):
        self.assertIsNone(ind.calculate_rsi(pd.Series([1.0, 2.0, 3.0])))

    def test_RSI_는_Wilder_정의를_따른다(self):
        closes = [100, 103, 101, 105, 104, 108, 106, 110, 107, 112, 109, 115, 111, 118, 113.0]
        deltas = [b - a for a, b in zip(closes, closes[1:])]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        expected_rs = (sum(gains) / 14) / (sum(losses) / 14)
        expected = 100 - 100 / (1 + expected_rs)
        self.assertAlmostEqual(ind.calculate_rsi(pd.Series(closes)), expected)

    def test_EMA_상수계열은_그_상수(self):
        self.assertAlmostEqual(ind.calculate_ema(pd.Series([100.0] * 40), 20), 100.0)

    def test_MACD_상승추세면_양수(self):
        macd, signal, hist = ind.calculate_macd(pd.Series([100.0 + i for i in range(60)]))
        self.assertGreater(macd, 0)

    def test_스토캐스틱_상승끝은_K가_100(self):
        up = pd.Series([100.0 + i for i in range(30)])
        k, d = ind.calculate_stochastic(up, up, up)
        self.assertAlmostEqual(k, 100.0)

    def test_스토캐스틱_보합은_0나눗셈없이_50(self):
        flat = pd.Series([100.0] * 30)
        k, d = ind.calculate_stochastic(flat, flat, flat)
        self.assertAlmostEqual(k, 50.0)

    def test_볼린저밴드_상단_중간_하단_순서(self):
        upper, middle, lower = ind.calculate_bollinger_bands(pd.Series([100.0 + i for i in range(40)]))
        self.assertGreater(upper, middle)
        self.assertGreater(middle, lower)

    def test_볼린저밴드_보합이면_세_값이_같다(self):
        upper, middle, lower = ind.calculate_bollinger_bands(pd.Series([100.0] * 40))
        self.assertAlmostEqual(upper, middle)
        self.assertAlmostEqual(middle, lower)

    def test_ATR_데이터_부족하면_None(self):
        one = pd.Series([1.0])
        self.assertIsNone(ind.calculate_atr(one, one, one))


# ======================================================================
# §4 매수 종목 선정
# ======================================================================

class BuySelectionTests(TestCase):
    def test_상승_종목만_상승률_순으로_선별(self):
        coins = [coin("KRW-A", 0.05), coin("KRW-B", -0.03), coin("KRW-C", 0.10)]
        result = ma.filter_rising_coins(coins)
        self.assertEqual([c["market"] for c in result], ["KRW-C", "KRW-A"])

    def test_상승률_상위_10개까지만(self):
        coins = [coin(f"KRW-{i}", 0.01 * (i + 1)) for i in range(20)]
        self.assertEqual(len(ma.filter_rising_coins(coins)), 10)

    def test_호가_매수세_우위_판정(self):
        has_pressure, spread = ma.analyze_orderbook(orderbook("KRW-A", bid=300, ask=100))
        self.assertTrue(has_pressure)

    def test_호가_매수세_부족하면_탈락(self):
        has_pressure, _ = ma.analyze_orderbook(orderbook("KRW-A", bid=140, ask=100))
        self.assertFalse(has_pressure)  # 1.5배 미만

    def test_스프레드가_넓으면_탈락(self):
        coins = [coin("KRW-A", 0.05)]
        wide = orderbook("KRW-A", bid_price=100.0, ask_price=101.0)  # 1% 스프레드
        with mock.patch("trading.market_analysis.get_orderbooks", return_value={"KRW-A": wide}):
            self.assertEqual(ma.filter_by_orderbook(coins), [])

    def test_호가_형식이_깨져도_예외없이_탈락(self):
        has_pressure, spread = ma.analyze_orderbook({"market": "KRW-A"})
        self.assertFalse(has_pressure)
        self.assertIsNone(spread)

    def test_최종_선정은_현재가x거래대금_최대(self):
        coins = [
            coin("KRW-A", 0.05, price=100, volume=1_000_000),
            coin("KRW-B", 0.04, price=500, volume=900_000),   # 곱이 가장 큼
        ]
        books = {c["market"]: orderbook(c["market"]) for c in coins}
        with mock.patch("trading.market_analysis.get_orderbooks", return_value=books):
            self.assertEqual(ma.select_buy_target(coins)["market"], "KRW-B")

    def test_제외_종목은_선정되지_않는다(self):
        coins = [coin("KRW-A", 0.05)]
        books = {"KRW-A": orderbook("KRW-A")}
        with mock.patch("trading.market_analysis.get_orderbooks", return_value=books):
            self.assertIsNone(ma.select_buy_target(coins, excluded_markets={"KRW-A"}))

    def test_상승_종목이_없으면_None(self):
        self.assertIsNone(ma.select_buy_target([coin("KRW-A", -0.05)]))


# ======================================================================
# §5 시장 강도 분석
# ======================================================================

class MarketStateTests(TestCase):
    def test_BTC_ETH_평균_2퍼센트_초과면_상승장(self):
        coins = [coin("KRW-BTC", 0.03), coin("KRW-ETH", 0.03)]
        self.assertEqual(ma.analyze_by_benchmark(coins), ma.BULLISH)

    def test_BTC_ETH_평균_마이너스2퍼센트_미만이면_하락장(self):
        coins = [coin("KRW-BTC", -0.03), coin("KRW-ETH", -0.03)]
        self.assertEqual(ma.analyze_by_benchmark(coins), ma.BEARISH)

    def test_상승코인_60퍼센트_초과면_상승장(self):
        coins = [coin(f"KRW-{i}", 0.01) for i in range(7)] + [coin(f"KRW-D{i}", -0.01) for i in range(3)]
        self.assertEqual(ma.analyze_by_up_down_ratio(coins), ma.BULLISH)

    def test_하락코인_60퍼센트_초과면_하락장(self):
        coins = [coin(f"KRW-{i}", -0.01) for i in range(7)] + [coin(f"KRW-U{i}", 0.01) for i in range(3)]
        self.assertEqual(ma.analyze_by_up_down_ratio(coins), ma.BEARISH)

    def test_거래량_20퍼센트_증가면_상승장(self):
        MarketVolumeRecord.objects.create(total_market_volume=100.0)
        MarketVolumeRecord.objects.update(recorded_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(ma.analyze_by_volume([coin("KRW-A", volume=200.0)]), ma.BULLISH)

    def test_기록이_없으면_보합(self):
        self.assertEqual(ma.analyze_by_volume([coin("KRW-A", volume=200.0)]), ma.NEUTRAL)

    def test_24시간이_지나야_새로_기록한다(self):
        MarketVolumeRecord.objects.create(total_market_volume=100.0)
        ma.analyze_by_volume([coin("KRW-A", volume=200.0)])
        self.assertEqual(MarketVolumeRecord.objects.count(), 1)

    def test_3개_중_2개가_같으면_그_방향(self):
        # BTC/ETH 상승 + 상승코인 비율 상승 -> 상승장
        coins = [coin("KRW-BTC", 0.03), coin("KRW-ETH", 0.03), coin("KRW-C", 0.01)]
        state, signals = ma.analyze_market_state(coins)
        self.assertEqual(state, ma.BULLISH)
        self.assertEqual(len(signals), 3)

    def test_의견이_갈리면_보합(self):
        coins = [coin("KRW-BTC", 0.0), coin("KRW-ETH", 0.0)]
        state, _ = ma.analyze_market_state(coins)
        self.assertEqual(state, ma.NEUTRAL)


# ======================================================================
# §3 주문
# ======================================================================

class UpbitOrderTests(TestCase):
    def post_and_capture(self, **kwargs):
        response = mock.Mock(status_code=201)
        response.json.return_value = {"uuid": "test-order"}
        with mock.patch("trading.utils.requests.post", return_value=response) as post:
            result = upbit_order(**kwargs)
        return result, post

    def test_매수는_side가_bid(self):
        _, post = self.post_and_capture(market="KRW-BTC", side="buy", price=10000, ord_type="price")
        self.assertEqual(post.call_args.kwargs["json"]["side"], "bid")

    def test_매도는_side가_ask이고_volume을_보낸다(self):
        _, post = self.post_and_capture(market="KRW-BTC", side="sell", volume=0.5, ord_type="market")
        params = post.call_args.kwargs["json"]
        self.assertEqual(params["side"], "ask")
        self.assertEqual(params["volume"], "0.5")

    def test_시장가_매도에_volume이_없으면_요청하지_않고_오류(self):
        result, post = self.post_and_capture(market="KRW-BTC", side="sell", ord_type="market")
        self.assertIn("error", result)
        post.assert_not_called()

    def test_인증_헤더에_access_key와_query_hash가_들어간다(self):
        _, post = self.post_and_capture(market="KRW-BTC", side="buy", price=10000, ord_type="price")
        token = post.call_args.kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        payload = jwt.decode(token, options={"verify_signature": False})
        self.assertIn("access_key", payload)
        self.assertIn("query_hash", payload)
        self.assertEqual(payload["query_hash_alg"], "SHA512")

    def test_네트워크_예외는_error로_감싼다(self):
        with mock.patch("trading.utils.requests.post",
                        side_effect=requests.RequestException("reset")):
            self.assertIn("error", upbit_order("KRW-BTC", "buy", price=10000, ord_type="price"))


class GetTickerPriceTests(TestCase):
    def test_현재가를_반환한다(self):
        response = mock.Mock()
        response.json.return_value = [{"market": "KRW-BTC", "trade_price": 90000000}]
        with mock.patch("trading.utils.requests.get", return_value=response):
            self.assertEqual(get_ticker_price("KRW-BTC"), 90000000)

    def test_요청_실패시_None(self):
        with mock.patch("trading.utils.requests.get",
                        side_effect=requests.RequestException("timeout")):
            self.assertIsNone(get_ticker_price("KRW-BTC"))


# ======================================================================
# §7 포지션 관리
# ======================================================================

class PositionTests(TestCase):
    def setUp(self):
        self.trader = AutoTrader(budget=10000)

    def test_매수시_DB와_메모리에_기록된다(self):
        self.trader.open_position("KRW-BTC", 100.0, "uuid-1", 10000)
        self.assertIn("KRW-BTC", self.trader.positions)
        self.assertTrue(TradeRecord.objects.filter(market="KRW-BTC", is_active=True).exists())

    def test_재시작하면_DB에서_복원한다(self):
        TradeRecord.objects.create(market="KRW-ETH", buy_price=50.0,
                                   highest_price=55.0, buy_krw_price=10000)
        restored = AutoTrader(budget=10000)
        self.assertIn("KRW-ETH", restored.positions)
        self.assertEqual(restored.positions["KRW-ETH"]["highest_price"], 55.0)

    def test_비활성_기록은_복원하지_않는다(self):
        TradeRecord.objects.create(market="KRW-ETH", buy_price=50.0, is_active=False)
        self.assertNotIn("KRW-ETH", AutoTrader(budget=10000).positions)

    def test_매도시_비활성화되고_매도기록이_남는다(self):
        self.trader.open_position("KRW-BTC", 100.0, "uuid-1", 10000)
        self.trader.close_position("KRW-BTC")
        self.assertNotIn("KRW-BTC", self.trader.positions)
        self.assertFalse(TradeRecord.objects.get(market="KRW-BTC").is_active)
        self.assertTrue(AskRecord.objects.filter(market="KRW-BTC").exists())

    def test_최고가는_메모리와_DB에_함께_갱신된다(self):
        self.trader.open_position("KRW-BTC", 100.0, "uuid-1", 10000)
        self.trader.update_highest_price("KRW-BTC", 120.0)
        self.assertEqual(self.trader.positions["KRW-BTC"]["highest_price"], 120.0)
        self.assertEqual(TradeRecord.objects.get(market="KRW-BTC").highest_price, 120.0)

    def test_사용자_수동매도를_감지해_정리한다(self):
        self.trader.open_position("KRW-BTC", 100.0, "uuid-1", 10000)
        # 매수 직후 유예 시간을 지난 상태로 만든다
        self.trader.positions["KRW-BTC"]["created_at"] = (
            timezone.now() - timedelta(seconds=60)
        )
        accounts = [{"currency": "KRW", "balance": "50000"}]  # BTC 없음
        self.trader.sync_manual_sells(accounts)
        self.assertNotIn("KRW-BTC", self.trader.positions)

    def test_계좌_조회_실패시엔_보유목록을_건드리지_않는다(self):
        self.trader.open_position("KRW-BTC", 100.0, "uuid-1", 10000)
        self.trader.sync_manual_sells({"error": {"message": "실패"}})
        self.assertIn("KRW-BTC", self.trader.positions)


class BlockedMarketTests(TestCase):
    def setUp(self):
        self.trader = AutoTrader(budget=10000)

    def test_주문_실패_종목은_제외된다(self):
        FailedMarket.objects.create(market="KRW-BAD")
        self.assertIn("KRW-BAD", self.trader.blocked_markets())

    def test_최근_매도_종목은_10분간_제외된다(self):
        AskRecord.objects.create(market="KRW-SOLD")
        self.assertIn("KRW-SOLD", self.trader.blocked_markets())

    def test_10분이_지난_매도_종목은_다시_매수_가능(self):
        AskRecord.objects.create(market="KRW-OLD")
        AskRecord.objects.update(recorded_at=timezone.now() - timedelta(seconds=700))
        self.assertNotIn("KRW-OLD", self.trader.blocked_markets())

    def test_보유_종목은_제외된다(self):
        self.trader.open_position("KRW-BTC", 100.0, "uuid-1", 10000)
        self.assertIn("KRW-BTC", self.trader.blocked_markets())


class TryBuyTests(TestCase):
    def setUp(self):
        self.trader = AutoTrader(budget=10000)
        self.accounts = [{"currency": "KRW", "balance": "100000"}]
        self.coins = [coin("KRW-A", 0.05)]

    def buy_with(self, order_result, accounts=None, coins=None):
        books = {"KRW-A": orderbook("KRW-A")}
        with mock.patch("trading.market_analysis.get_orderbooks", return_value=books), \
             mock.patch("trading.auto_trade.upbit_order", return_value=order_result) as order:
            self.trader.try_buy(accounts or self.accounts, coins or self.coins)
        return order

    def test_조건을_만족하면_시장가_매수한다(self):
        order = self.buy_with({"uuid": "u1"})
        order.assert_called_once()
        self.assertEqual(order.call_args.kwargs["ord_type"], "price")
        self.assertIn("KRW-A", self.trader.positions)

    def test_잔고가_예산보다_적으면_잔고만큼만_주문한다(self):
        order = self.buy_with({"uuid": "u1"}, accounts=[{"currency": "KRW", "balance": "12000"}])
        self.assertEqual(order.call_args.kwargs["price"], 10000)

        self.trader.positions.clear()
        order = self.buy_with({"uuid": "u2"}, accounts=[{"currency": "KRW", "balance": "10500"}])
        self.assertEqual(order.call_args.kwargs["price"], 10000)

    def test_최소잔고_미만이면_매수하지_않는다(self):
        order = self.buy_with({"uuid": "u1"}, accounts=[{"currency": "KRW", "balance": "9000"}])
        order.assert_not_called()

    def test_동시보유_상한에_도달하면_매수하지_않는다(self):
        for i in range(MAX_POSITIONS):
            self.trader.open_position(f"KRW-P{i}", 100.0, f"u{i}", 10000)
        order = self.buy_with({"uuid": "u9"})
        order.assert_not_called()

    def test_주문_실패시_FailedMarket에_기록한다(self):
        self.buy_with({"error": {"message": "실패"}})
        self.assertTrue(FailedMarket.objects.filter(market="KRW-A").exists())
        self.assertNotIn("KRW-A", self.trader.positions)


# ======================================================================
# §8 매도 전략
# ======================================================================

class SellDecisionTests(TestCase):
    """ 매도 판단: 시장 상태와 무관하게 매수가 기준 고정 ±2% """

    def setUp(self):
        self.trader = AutoTrader(budget=10000)
        self.trader.open_position("KRW-A", 100.0, "u1", 10000)
        self.position = self.trader.positions["KRW-A"]

    def decide(self, current_price, coin_info=None):
        change = current_price / 100.0 - 1
        return self.trader.decide_sell("KRW-A", self.position, current_price, change, coin_info)

    def test_2퍼센트_오르면_익절(self):
        self.assertIn("익절", self.decide(102.0))

    def test_2퍼센트_내리면_손절(self):
        self.assertIn("손절", self.decide(98.0))

    def test_2퍼센트_미만_변동은_보유(self):
        self.assertIsNone(self.decide(101.9))
        self.assertIsNone(self.decide(98.1))

    def test_상승장에서도_2퍼센트면_판다(self):
        self.trader.market_state = ma.BULLISH
        self.assertIn("익절", self.decide(102.0))

    def test_하락장에서도_손절선은_2퍼센트(self):
        self.trader.market_state = ma.BEARISH
        self.assertIsNone(self.decide(98.5))     # -1.5% 는 보유
        self.assertIn("손절", self.decide(98.0))

    def test_고변동성_종목도_손절선은_2퍼센트(self):
        volatile = coin("KRW-A", change_rate=0.08)  # 전일 +8% 종목
        self.assertIn("손절", self.decide(98.0, volatile))

    def test_1퍼센트_상승으로는_팔지_않는다(self):
        self.trader.market_state = ma.NEUTRAL
        self.assertIsNone(self.decide(101.0))

    def test_최고점_대비_하락만으로는_팔지_않는다(self):
        # 101.9 까지 올랐다가 100.5 로 밀려도 ±2% 안이면 보유
        self.trader.update_highest_price("KRW-A", 101.9)
        self.assertIsNone(self.decide(100.5))

    def test_보유_시간은_매도에_영향이_없다(self):
        self.position["created_at"] = timezone.now() - timedelta(hours=2)
        self.assertIsNone(self.decide(101.0))


class SellExecutionTests(TestCase):
    def setUp(self):
        self.trader = AutoTrader(budget=10000)
        self.trader.open_position("KRW-A", 100.0, "u1", 10000)

    def test_보유수량을_조회해_volume으로_넘긴다(self):
        with mock.patch("trading.auto_trade.get_balance", return_value=0.25) as balance, \
             mock.patch("trading.auto_trade.is_order_done", return_value=True), \
             mock.patch("trading.auto_trade.upbit_order", return_value={"uuid": "x"}) as order:
            self.trader.sell_all("KRW-A", 102.0, "매도")

        balance.assert_called_once_with("A")
        self.assertEqual(order.call_args.kwargs["volume"], 0.25)
        self.assertNotIn("KRW-A", self.trader.positions)

    def test_주문이_실패하면_보유상태를_유지한다(self):
        with mock.patch("trading.auto_trade.get_balance", return_value=0.25), \
             mock.patch("trading.auto_trade.upbit_order", return_value={"error": {"m": "x"}}):
            self.trader.sell_all("KRW-A", 102.0, "매도")
        self.assertIn("KRW-A", self.trader.positions)

    def test_보유수량이_없으면_주문없이_정리한다(self):
        with mock.patch("trading.auto_trade.get_balance", return_value=0.0), \
             mock.patch("trading.auto_trade.upbit_order") as order:
            self.trader.sell_all("KRW-A", 102.0, "매도")
        order.assert_not_called()
        self.assertNotIn("KRW-A", self.trader.positions)


# ======================================================================
# §12 수수료 / 일일 손실 한도
# ======================================================================

class ProfitAndLossCutTests(TestCase):
    def setUp(self):
        self.trader = AutoTrader(budget=10000)
        # 기준 총자산 10,000원 → 한도 -1,000원
        self.trader.equity_base = 10000.0

    def test_한도는_기준_총자산의_10퍼센트(self):
        self.assertEqual(self.trader.daily_loss_limit(), -1000.0)

    def test_한도는_매수금액과_무관하다(self):
        """ 매수금액을 바꿔도 한도는 기준 총자산으로만 정해진다 """
        self.trader.budget = 500000
        self.assertEqual(self.trader.daily_loss_limit(), -1000.0)

    def test_실현손익은_왕복_수수료를_반영한다(self):
        pnl = self.trader.record_trade_result("KRW-A", 100.0, 102.0, 10000)
        effective_buy = 100.0 * (1 + FEE_RATE)
        effective_sell = 102.0 * (1 - FEE_RATE)
        expected = 10000 * (effective_sell - effective_buy) / effective_buy
        self.assertAlmostEqual(pnl, expected)

    def test_수수료를_빼면_2퍼센트_수익이_2퍼센트에_못미친다(self):
        pnl = self.trader.record_trade_result("KRW-A", 100.0, 102.0, 10000)
        self.assertLess(pnl, 200.0)

    def test_한도_미도달이면_계속_매매한다(self):
        self.trader.active = True
        self.trader.daily_pnl = -999.0
        self.assertFalse(self.trader.check_loss_cut())
        self.assertTrue(self.trader.active)

    def test_한도_도달하면_정지한다(self):
        self.trader.active = True
        self.trader.daily_pnl = -1000.0
        self.assertTrue(self.trader.check_loss_cut())
        self.assertFalse(self.trader.active)

    def test_한도_도달시_보유_전량을_청산한다(self):
        self.trader.active = True
        self.trader.daily_pnl = -1200.0
        self.trader.open_position("KRW-A", 100.0, "u1", 10000)
        self.trader.open_position("KRW-B", 100.0, "u2", 10000)

        with mock.patch("trading.auto_trade.get_ticker_price", return_value=90.0), \
             mock.patch.object(self.trader, "sell_all") as sell_all:
            self.trader.check_loss_cut()

        self.assertEqual(sell_all.call_count, 2)

    def test_날짜가_바뀌면_당일_손익이_초기화된다(self):
        self.trader.daily_pnl = -900.0
        self.trader.pnl_date = date(2020, 1, 1)
        self.trader.reset_daily_pnl_if_new_day()
        self.assertEqual(self.trader.daily_pnl, 0.0)

    def test_초기화_기준일은_KST_00시다(self):
        """ 자정(KST) 을 넘기는 순간 새 날짜가 되어 손익이 초기화된다 """
        from trading.auto_trade import KST, today_kst

        before = datetime(2026, 3, 4, 23, 59, 59, tzinfo=KST)
        after = datetime(2026, 3, 5, 0, 0, 1, tzinfo=KST)

        with mock.patch("trading.auto_trade.datetime") as dt:
            dt.now.return_value = before
            self.assertEqual(today_kst(), date(2026, 3, 4))
            dt.now.return_value = after
            self.assertEqual(today_kst(), date(2026, 3, 5))

    def test_자정을_넘기면_한도_판정도_새로_센다(self):
        self.trader.daily_pnl = -900.0
        self.trader.run_start_pnl = 0.0
        self.trader.pnl_date = date(2020, 1, 1)
        self.trader.reset_daily_pnl_if_new_day()
        self.assertEqual(self.trader.run_pnl(), 0.0)

    def test_같은_날에는_손익이_유지된다(self):
        self.trader.daily_pnl = -900.0
        self.trader.reset_daily_pnl_if_new_day()
        self.assertEqual(self.trader.daily_pnl, -900.0)


# ======================================================================
# §10 웹 엔드포인트
# ======================================================================

class EndpointTests(TestCase):
    def tearDown(self):
        # 시작 엔드포인트가 설정한 전역 트레이더가 다른 테스트로 새지 않게 한다
        views.trader = None
        views.trader_thread = None

    def test_모든_조회_엔드포인트가_JSON을_반환한다(self):
        paths = [
            "/api/trade_logs/",
            "/api/check_auto_trading/",
            "/api/getRecntTradeLog/",
            "/api/recentProfitLog/",
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/json")

    def test_시장상태_엔드포인트(self):
        coins = [coin("KRW-BTC", 0.03), coin("KRW-ETH", 0.03)]
        with mock.patch("trading.views.get_krw_market_coin_info", return_value=coins):
            response = self.client.get("/api/get_market_volume/")
        self.assertEqual(response.json()["state"], ma.BULLISH)

    def test_시세조회_실패시_unknown(self):
        with mock.patch("trading.views.get_krw_market_coin_info", return_value=[]):
            response = self.client.get("/api/get_market_volume/")
        self.assertEqual(response.json()["state"], "unknown")

    def test_계좌_엔드포인트(self):
        with mock.patch("trading.views.get_account_info", return_value=[]):
            response = self.client.get("/api/fetch_account_data/")
        self.assertEqual(response.status_code, 200)

    def test_시작_요청이_NameError_없이_처리된다(self):
        with mock.patch("trading.views.AutoTrader") as trader_cls, \
             mock.patch("trading.views.threading.Thread"):
            trader_cls.return_value.active = False
            trader_cls.return_value.daily_pnl = 0.0
            trader_cls.return_value.equity_base = 10000.0
            trader_cls.return_value.daily_loss_limit.return_value = -1000.0
            trader_cls.return_value.loss_cut_reached.return_value = False
            response = self.client.get("/auto_trade/start/", {"budget": "10000"})
        self.assertEqual(response.json()["status"], "started")

    def test_잘못된_budget은_400(self):
        self.assertEqual(self.client.get("/auto_trade/start/", {"budget": "만원"}).status_code, 400)

    def test_정지_요청은_실행중이_아니면_not_running(self):
        self.assertEqual(self.client.get("/auto_trade/stop/").json()["status"], "not running")

    def test_메인_페이지가_렌더링된다(self):
        with mock.patch("trading.views.get_account_info", return_value=[]), \
             mock.patch("trading.views.get_top_coin_info", return_value=[]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)


# ======================================================================
# 점검에서 발견된 버그들의 회귀 테스트
# ======================================================================

class BalanceFailureSafetyTests(TestCase):
    """ 버그 1: 계좌 조회 실패를 잔고 0 으로 오판해 포지션을 버리던 문제 """

    def setUp(self):
        self.trader = AutoTrader(budget=10000)
        self.trader.open_position("KRW-BTC", 100.0, "u1", 10000)

    def test_계좌_조회_실패시_매도하지_않고_포지션을_유지한다(self):
        with mock.patch("trading.auto_trade.get_balance", return_value=None), \
             mock.patch("trading.auto_trade.upbit_order") as order:
            self.trader.sell_all("KRW-BTC", 98.0, "손절")

        order.assert_not_called()
        self.assertIn("KRW-BTC", self.trader.positions)          # 포지션 유지
        self.assertTrue(TradeRecord.objects.get(market="KRW-BTC").is_active)

    def test_실제_잔고_0이면_기존대로_정리한다(self):
        with mock.patch("trading.auto_trade.get_balance", return_value=0.0), \
             mock.patch("trading.auto_trade.upbit_order") as order:
            self.trader.sell_all("KRW-BTC", 98.0, "손절")

        order.assert_not_called()
        self.assertNotIn("KRW-BTC", self.trader.positions)


class ManualSellGraceTests(TestCase):
    """ 버그 2: 매수 직후 체결 반영 전에 수동 매도로 오판하던 문제 """

    def setUp(self):
        self.trader = AutoTrader(budget=10000)
        self.trader.open_position("KRW-XRP", 100.0, "u1", 10000)

    def test_매수_직후에는_잔고에_없어도_정리하지_않는다(self):
        accounts = [{"currency": "KRW", "balance": "50000"}]  # XRP 아직 미반영
        self.trader.sync_manual_sells(accounts)
        self.assertIn("KRW-XRP", self.trader.positions)

    def test_유예시간이_지나면_수동_매도로_판정한다(self):
        self.trader.positions["KRW-XRP"]["created_at"] = timezone.now() - timedelta(seconds=60)
        accounts = [{"currency": "KRW", "balance": "50000"}]
        self.trader.sync_manual_sells(accounts)
        self.assertNotIn("KRW-XRP", self.trader.positions)

    def test_locked_수량도_보유로_취급한다(self):
        from trading.utils import get_held_currencies
        accounts = [
            {"currency": "KRW", "balance": "50000"},
            {"currency": "XRP", "balance": "0", "locked": "10.5"},  # 주문 중 잠금
        ]
        self.assertIn("XRP", get_held_currencies(accounts))


class BuyFeeHeadroomTests(TestCase):
    """ 버그 3: 수수료 헤드룸 없이 전액 주문하다 거부되던 문제 """

    def setUp(self):
        self.trader = AutoTrader(budget=20000)

    def buy_with(self, balance, order_result=None):
        accounts = [{"currency": "KRW", "balance": str(balance)}]
        coins = [coin("KRW-A", 0.05)]
        books = {"KRW-A": orderbook("KRW-A")}
        with mock.patch("trading.market_analysis.get_orderbooks", return_value=books), \
             mock.patch("trading.auto_trade.upbit_order",
                        return_value=order_result or {"uuid": "u1"}) as order:
            self.trader.try_buy(accounts, coins)
        return order

    def test_잔고가_예산보다_적으면_수수료만큼_뺀_금액으로_주문한다(self):
        order = self.buy_with(15000)
        # 15000 / 1.0005 = 14992.xx -> 14992
        self.assertEqual(order.call_args.kwargs["price"], 14992)

    def test_잔고가_충분하면_예산_전액을_주문한다(self):
        order = self.buy_with(100000)
        self.assertEqual(order.call_args.kwargs["price"], 20000)

    def test_일시적_오류는_실패_목록에_올리지_않는다(self):
        self.buy_with(100000, order_result={
            "error": {"message": "주문 요청 실패: timeout"}, "transient": True,
        })
        self.assertFalse(FailedMarket.objects.filter(market="KRW-A").exists())

    def test_API_거부는_실패_목록에_올린다(self):
        self.buy_with(100000, order_result={"error": {"name": "invalid_market"}})
        self.assertTrue(FailedMarket.objects.filter(market="KRW-A").exists())


class FailedMarketExpiryTests(TestCase):
    """ 버그 4: 주문 실패 종목이 영구 제외되던 문제 """

    def setUp(self):
        self.trader = AutoTrader(budget=10000)

    def test_1시간_이내_실패_종목은_제외된다(self):
        FailedMarket.objects.create(market="KRW-BAD")
        self.assertIn("KRW-BAD", self.trader.blocked_markets())

    def test_1시간이_지나면_다시_매수_가능하다(self):
        FailedMarket.objects.create(market="KRW-OLD")
        FailedMarket.objects.update(failed_at=timezone.now() - timedelta(seconds=3700))
        self.assertNotIn("KRW-OLD", self.trader.blocked_markets())


class RebuyResetsHoldTimeTests(TestCase):
    """ 버그 5: 재매수 시 이전 매수 시각이 남아 보유 시간이 왜곡되던 문제 """

    def test_같은_종목을_다시_사면_매수_시각이_초기화된다(self):
        trader = AutoTrader(budget=10000)
        trader.open_position("KRW-ETH", 100.0, "u1", 10000)
        # 이틀 전에 산 것처럼 조작 후 종료
        TradeRecord.objects.filter(market="KRW-ETH").update(
            created_at=timezone.now() - timedelta(days=2)
        )
        trader.close_position("KRW-ETH")

        # 재매수
        trader.open_position("KRW-ETH", 200.0, "u2", 10000)

        held_seconds = (
            timezone.now() - trader.positions["KRW-ETH"]["created_at"]
        ).total_seconds()
        self.assertLess(held_seconds, 5)  # 방금 산 것으로 계산되어야 한다

        db_created = TradeRecord.objects.get(market="KRW-ETH").created_at
        self.assertLess((timezone.now() - db_created).total_seconds(), 5)


class TotalEquityTests(TestCase):
    """ 손실 한도의 기준이 되는 총자산 계산 """

    def setUp(self):
        self.trader = AutoTrader(budget=10000)

    def test_KRW_잔고만_있으면_그대로다(self):
        accounts = [{"currency": "KRW", "balance": "1000000", "locked": "0"}]
        self.assertEqual(self.trader.total_equity(accounts), 1000000.0)

    def test_보유_코인은_현재가로_평가한다(self):
        accounts = [
            {"currency": "KRW", "balance": "500000", "locked": "0"},
            {"currency": "BTC", "balance": "0.01", "locked": "0"},
        ]
        with mock.patch("trading.auto_trade.get_ticker_price", return_value=100000000.0):
            # 500,000 + 0.01 * 100,000,000 = 1,500,000
            self.assertEqual(self.trader.total_equity(accounts), 1500000.0)

    def test_주문중으로_잠긴_수량도_자산에_넣는다(self):
        accounts = [
            {"currency": "KRW", "balance": "100000", "locked": "50000"},
            {"currency": "XRP", "balance": "0", "locked": "10"},
        ]
        with mock.patch("trading.auto_trade.get_ticker_price", return_value=2000.0):
            # (100,000 + 50,000) + 10 * 2,000 = 170,000
            self.assertEqual(self.trader.total_equity(accounts), 170000.0)

    def test_시세를_못_구한_코인은_건너뛴다(self):
        accounts = [
            {"currency": "KRW", "balance": "100000", "locked": "0"},
            {"currency": "NOPE", "balance": "5", "locked": "0"},
        ]
        with mock.patch("trading.auto_trade.get_ticker_price", return_value=None):
            self.assertEqual(self.trader.total_equity(accounts), 100000.0)

    def test_계좌_조회_실패는_0이다(self):
        self.assertEqual(self.trader.total_equity({"error": {"message": "실패"}}), 0.0)

    def test_기준_총자산의_10퍼센트가_한도가_된다(self):
        accounts = [{"currency": "KRW", "balance": "2000000", "locked": "0"}]
        with mock.patch.object(self.trader, "total_equity", return_value=2000000.0):
            self.trader.begin_run()
        self.assertEqual(self.trader.equity_base, 2000000.0)
        self.assertEqual(self.trader.daily_loss_limit(), -200000.0)


class DailyPnlPersistenceTests(TestCase):
    """ 당일 손익 보존과, 손실 한도 정지 후 재시작 """

    def start(self, budget, equity):
        """ 총자산을 고정한 채 시작 엔드포인트를 호출한다 """
        views.trader = None
        with mock.patch.object(AutoTrader, "total_equity", return_value=equity), \
             mock.patch("trading.views.threading.Thread"):
            return self.client.get("/auto_trade/start/", {"budget": str(budget)}).json()

    def test_실현손익이_DB에_기록된다(self):
        trader = AutoTrader(budget=10000)
        trader.record_trade_result("KRW-A", 100.0, 98.0, 10000)
        record = DailyPnlRecord.objects.get(date=trader.pnl_date)
        self.assertAlmostEqual(record.realized_pnl, trader.daily_pnl)

    def test_새_트레이더가_당일_손익을_복원한다(self):
        first = AutoTrader(budget=10000)
        first.equity_base = 10000.0
        for _ in range(5):
            first.record_trade_result("KRW-A", 100.0, 98.0, 10000)

        # 서버 재시작을 흉내 내 새 트레이더 생성
        second = AutoTrader(budget=10000)
        self.assertAlmostEqual(second.daily_pnl, first.daily_pnl)

    def test_한도_정지_후_다시_시작할_수_있다(self):
        """ 손실 한도로 멈춰도 재시작이 거부되지 않는다 """
        trader = AutoTrader(budget=10000)
        trader.equity_base = 10000.0
        trader.active = True
        for _ in range(5):
            trader.record_trade_result("KRW-A", 100.0, 98.0, 10000)
        self.assertTrue(trader.check_loss_cut())
        self.assertFalse(trader.active)

        self.assertEqual(self.start(10000, 9000.0)["status"], "started")

    def test_재시작하면_한도_판정이_새_구간에서_시작된다(self):
        """ 당일 누적 손익은 남지만, 한도는 재시작 시점부터 다시 센다 """
        trader = AutoTrader(budget=10000)
        trader.equity_base = 10000.0
        for _ in range(5):
            trader.record_trade_result("KRW-A", 100.0, 98.0, 10000)
        losses = trader.daily_pnl
        self.assertLessEqual(losses, -1000.0)

        self.assertEqual(self.start(10000, 9000.0)["status"], "started")
        restarted = views.trader
        self.assertAlmostEqual(restarted.daily_pnl, losses)   # 당일 누적은 그대로
        self.assertEqual(restarted.run_pnl(), 0.0)            # 판정은 0 에서 다시
        self.assertFalse(restarted.loss_cut_reached())

    def test_재시작하면_기준_총자산을_그_시점_값으로_다시_잡는다(self):
        self.assertEqual(self.start(10000, 900000.0)["status"], "started")
        self.assertEqual(views.trader.equity_base, 900000.0)
        self.assertEqual(views.trader.daily_loss_limit(), -90000.0)

        self.assertEqual(self.start(10000, 500000.0)["status"], "started")
        self.assertEqual(views.trader.equity_base, 500000.0)
        self.assertEqual(views.trader.daily_loss_limit(), -50000.0)

    def test_총자산_조회_실패면_시작하지_않는다(self):
        """ 한도를 정할 수 없는 채로 매매를 시작하지 않는다 """
        result = self.start(10000, 0.0)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(views.trader)

    def test_기준_총자산을_모르면_로스컷을_판정하지_않는다(self):
        trader = AutoTrader(budget=10000)
        trader.equity_base = 0.0
        trader.daily_pnl = -999999.0
        self.assertFalse(trader.loss_cut_reached())

    def test_트레이더가_없어도_대시보드가_당일_손익을_보여준다(self):
        """ 서버 재시작 후 0원으로 표시되어 실제 손익을 감추던 문제 """
        trader = AutoTrader(budget=10000)
        trader.equity_base = 330000.0
        for _ in range(5):
            trader.record_trade_result("KRW-A", 100.0, 98.0, 330000)

        views.trader = None
        data = self.client.get("/api/check_auto_trading/").json()
        self.assertEqual(data["daily_pnl"], round(trader.daily_pnl))
        self.assertEqual(data["daily_loss_limit"], -33000)

    def test_다른_날짜에는_영향이_없다(self):
        DailyPnlRecord.objects.create(
            date=date(2020, 1, 1), realized_pnl=-99999.0
        )
        trader = AutoTrader(budget=10000)
        self.assertEqual(trader.daily_pnl, 0.0)


class SellAllPnlRecoveryTests(TestCase):
    """ 버그 6: 로스컷 청산 시 시세 조회 실패로 손익이 누락되던 문제 """

    def test_시세가_None이면_한_번_더_조회해_손익을_기록한다(self):
        trader = AutoTrader(budget=10000)
        trader.open_position("KRW-A", 100.0, "u1", 10000)

        with mock.patch("trading.auto_trade.get_balance", return_value=1.0), \
             mock.patch("trading.auto_trade.is_order_done", return_value=True), \
             mock.patch("trading.auto_trade.upbit_order", return_value={"uuid": "x"}), \
             mock.patch("trading.auto_trade.get_ticker_price", return_value=90.0):
            trader.sell_all("KRW-A", None, "🚨 로스컷 청산")

        self.assertLess(trader.daily_pnl, 0)  # 손실이 누적에 반영됨


class ThreadSafetyTests(TestCase):
    """ 버그 8: 매매 스레드와 웹 요청 스레드의 경합 """

    def test_positions_snapshot은_사본을_반환한다(self):
        trader = AutoTrader(budget=10000)
        trader.open_position("KRW-A", 100.0, "u1", 10000)
        snapshot = trader.positions_snapshot()
        snapshot.append("KRW-FAKE")
        self.assertNotIn("KRW-FAKE", trader.positions)

    def test_순회_중_변경에도_안전하다(self):
        import threading as th
        trader = AutoTrader(budget=10000)
        errors = []

        def mutate():
            for i in range(300):
                trader.open_position(f"KRW-T{i % 5}", 100.0, None, 10000)
                trader.close_position(f"KRW-T{i % 5}")

        def read():
            try:
                for _ in range(300):
                    trader.positions_snapshot()
            except RuntimeError as e:
                errors.append(e)

        threads = [th.Thread(target=mutate), th.Thread(target=read)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
