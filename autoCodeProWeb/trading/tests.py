# trading/tests.py
from unittest import mock

import jwt
import requests
from django.test import TestCase

from .auto_trade import AutoTrader, RSI_PERIOD
from .utils import get_ticker_price, upbit_order


def make_candles(closes):
    """ 시간 순 종가 리스트를 업비트 응답 형식(최신 캔들 우선)으로 변환 """
    return [{"trade_price": price} for price in reversed(closes)]


class GetRsiTests(TestCase):
    """ AutoTrader.get_rsi 검증 """

    def setUp(self):
        self.trader = AutoTrader(budget=10000)

    def call_with_candles(self, candles):
        response = mock.Mock()
        response.json.return_value = candles
        with mock.patch("trading.auto_trade.requests.get", return_value=response):
            return self.trader.get_rsi("KRW-BTC")

    def test_상승만_있으면_100(self):
        closes = [100 + i for i in range(RSI_PERIOD + 1)]
        self.assertEqual(self.call_with_candles(make_candles(closes)), 100.0)

    def test_하락만_있으면_0(self):
        closes = [100 - i for i in range(RSI_PERIOD + 1)]
        self.assertEqual(self.call_with_candles(make_candles(closes)), 0.0)

    def test_상승과_하락이_같으면_50(self):
        # 100, 101, 100, 101 ... 로 상승 7회 / 하락 7회
        closes = [100 + (i % 2) for i in range(RSI_PERIOD + 1)]
        self.assertAlmostEqual(self.call_with_candles(make_candles(closes)), 50.0)

    def test_가격_변동이_없으면_50(self):
        closes = [100] * (RSI_PERIOD + 1)
        self.assertEqual(self.call_with_candles(make_candles(closes)), 50.0)

    def test_최신_캔들_우선_순서를_뒤집어_계산한다(self):
        # 시간 순으로는 계속 상승 -> 100 이어야 한다.
        # 뒤집지 않으면 계속 하락으로 읽혀 0 이 나온다.
        closes = [100 + i for i in range(RSI_PERIOD + 1)]
        candles = make_candles(closes)
        self.assertGreater(candles[0]["trade_price"], candles[-1]["trade_price"])
        self.assertEqual(self.call_with_candles(candles), 100.0)

    def test_캔들이_부족하면_None(self):
        closes = [100 + i for i in range(RSI_PERIOD)]  # period+1 보다 하나 부족
        self.assertIsNone(self.call_with_candles(make_candles(closes)))

    def test_요청_실패시_None(self):
        with mock.patch(
            "trading.auto_trade.requests.get",
            side_effect=requests.RequestException("timeout"),
        ):
            self.assertIsNone(self.trader.get_rsi("KRW-BTC"))

    def test_RSI_는_0과_100_사이(self):
        closes = [100, 103, 101, 105, 104, 108, 106, 110, 107, 112, 109, 115, 111, 118, 113]
        rsi = self.call_with_candles(make_candles(closes))
        self.assertGreaterEqual(rsi, 0)
        self.assertLessEqual(rsi, 100)


class UpbitOrderTests(TestCase):
    """ utils.upbit_order 의 요청 구성 검증 """

    def post_and_capture(self, **kwargs):
        response = mock.Mock(status_code=201)
        response.json.return_value = {"uuid": "test-order"}
        with mock.patch("trading.utils.requests.post", return_value=response) as post:
            result = upbit_order(**kwargs)
        return result, post

    def test_매수는_side가_bid(self):
        _, post = self.post_and_capture(
            market="KRW-BTC", side="buy", price=10000, ord_type="price"
        )
        params = post.call_args.kwargs["json"]
        self.assertEqual(params["side"], "bid")
        self.assertEqual(params["ord_type"], "price")
        self.assertEqual(params["price"], "10000")

    def test_매도는_side가_ask이고_volume을_보낸다(self):
        _, post = self.post_and_capture(
            market="KRW-BTC", side="sell", volume=0.5, ord_type="market"
        )
        params = post.call_args.kwargs["json"]
        self.assertEqual(params["side"], "ask")
        self.assertEqual(params["ord_type"], "market")
        self.assertEqual(params["volume"], "0.5")

    def test_시장가_매도에_volume이_없으면_요청하지_않고_오류(self):
        result, post = self.post_and_capture(
            market="KRW-BTC", side="sell", ord_type="market"
        )
        self.assertIn("error", result)
        self.assertIn("volume", result["error"]["message"])
        post.assert_not_called()

    def test_시장가_매수에_price가_없으면_요청하지_않고_오류(self):
        result, post = self.post_and_capture(
            market="KRW-BTC", side="buy", ord_type="price"
        )
        self.assertIn("error", result)
        self.assertIn("price", result["error"]["message"])
        post.assert_not_called()

    def test_잘못된_side는_오류(self):
        result, post = self.post_and_capture(
            market="KRW-BTC", side="ask", price=10000, ord_type="price"
        )
        self.assertIn("error", result)
        post.assert_not_called()

    def test_인증_헤더에_access_key와_query_hash가_들어간다(self):
        _, post = self.post_and_capture(
            market="KRW-BTC", side="buy", price=10000, ord_type="price"
        )
        token = post.call_args.kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        payload = jwt.decode(token, options={"verify_signature": False})

        self.assertIn("access_key", payload)
        self.assertIn("nonce", payload)
        self.assertIn("query_hash", payload)
        self.assertEqual(payload["query_hash_alg"], "SHA512")

    def test_HTTP_오류는_error로_감싼다(self):
        response = mock.Mock(status_code=400)
        response.json.return_value = {"error": {"name": "insufficient_funds"}}
        with mock.patch("trading.utils.requests.post", return_value=response):
            result = upbit_order("KRW-BTC", "buy", price=10000, ord_type="price")
        self.assertIn("error", result)

    def test_네트워크_예외는_error로_감싼다(self):
        with mock.patch(
            "trading.utils.requests.post",
            side_effect=requests.RequestException("connection reset"),
        ):
            result = upbit_order("KRW-BTC", "buy", price=10000, ord_type="price")
        self.assertIn("error", result)


class StartAutoTradingViewTests(TestCase):
    """ 자동매매 시작/정지 뷰 검증 """

    def test_시작_요청이_NameError_없이_처리된다(self):
        with mock.patch("trading.views.AutoTrader") as trader_cls, \
             mock.patch("trading.views.threading.Thread"):
            trader_cls.return_value.active = False
            response = self.client.get("/auto_trade/start/", {"budget": "10000"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "started")

    def test_잘못된_budget은_400(self):
        response = self.client.get("/auto_trade/start/", {"budget": "만원"})
        self.assertEqual(response.status_code, 400)

    def test_정지_요청은_실행중이_아니면_not_running(self):
        response = self.client.get("/auto_trade/stop/")
        self.assertEqual(response.json()["status"], "not running")


class CheckSellTests(TestCase):
    """ 매도 판단 로직 검증 """

    def setUp(self):
        self.trader = AutoTrader(budget=10000)
        self.trader.current_order = {"market": "KRW-BTC", "buy_price": 100.0}
        self.trader.highest_price = 100.0

    def run_check(self, current_price):
        with mock.patch(
            "trading.auto_trade.get_ticker_price", return_value=current_price
        ), mock.patch.object(self.trader, "sell_all") as sell_all:
            self.trader.check_sell()
        return sell_all

    def test_상위5개_목록과_무관하게_보유종목_시세를_직접_조회한다(self):
        with mock.patch(
            "trading.auto_trade.get_ticker_price", return_value=100.0
        ) as ticker, mock.patch(
            "trading.auto_trade.get_krw_market_coin_info"
        ) as coin_list:
            self.trader.check_sell()

        ticker.assert_called_once_with("KRW-BTC")
        coin_list.assert_not_called()

    def test_최고점_대비_1퍼센트_하락하면_매도(self):
        self.trader.highest_price = 110.0
        sell_all = self.run_check(108.0)  # 110 * 0.99 = 108.9 >= 108
        sell_all.assert_called_once()
        self.assertIn("트레일링 스탑", sell_all.call_args.args[2])

    def test_매수가_대비_3퍼센트_하락하면_손절(self):
        # 최고점을 매수가와 같게 두어 트레일링 스탑이 아닌 손절 경로를 태운다
        self.trader.highest_price = 96.0
        sell_all = self.run_check(96.0)  # 100 * 0.97 = 97 >= 96
        sell_all.assert_called_once()

    def test_조건_미달이면_매도하지_않는다(self):
        sell_all = self.run_check(100.0)
        sell_all.assert_not_called()

    def test_상승하면_최고점을_갱신한다(self):
        self.run_check(120.0)
        self.assertEqual(self.trader.highest_price, 120.0)

    def test_현재가_조회_실패시_아무것도_하지_않는다(self):
        sell_all = self.run_check(None)
        sell_all.assert_not_called()
        self.assertIsNotNone(self.trader.current_order)


class SellAllTests(TestCase):
    """ 전량 시장가 매도 검증 """

    def setUp(self):
        self.trader = AutoTrader(budget=10000)
        self.trader.current_order = {"market": "KRW-BTC", "buy_price": 100.0}
        self.trader.highest_price = 120.0

    def test_보유수량을_조회해_volume으로_넘긴다(self):
        with mock.patch("trading.auto_trade.get_balance", return_value=0.25) as balance, \
             mock.patch("trading.auto_trade.upbit_order", return_value={"uuid": "x"}) as order:
            self.trader.sell_all("KRW-BTC", 110.0, "매도")

        balance.assert_called_once_with("BTC")
        self.assertEqual(order.call_args.kwargs["volume"], 0.25)
        self.assertEqual(order.call_args.kwargs["ord_type"], "market")
        self.assertIsNone(self.trader.current_order)

    def test_보유수량이_없으면_주문하지_않고_상태를_초기화한다(self):
        with mock.patch("trading.auto_trade.get_balance", return_value=0.0), \
             mock.patch("trading.auto_trade.upbit_order") as order:
            self.trader.sell_all("KRW-BTC", 110.0, "매도")

        order.assert_not_called()
        self.assertIsNone(self.trader.current_order)

    def test_주문이_실패하면_보유상태를_유지한다(self):
        with mock.patch("trading.auto_trade.get_balance", return_value=0.25), \
             mock.patch("trading.auto_trade.upbit_order",
                        return_value={"error": {"message": "실패"}}):
            self.trader.sell_all("KRW-BTC", 110.0, "매도")

        # 매도가 안 됐는데 상태를 지우면 보유 코인을 영영 못 판다
        self.assertIsNotNone(self.trader.current_order)


class GetTickerPriceTests(TestCase):
    """ utils.get_ticker_price 검증 """

    def test_현재가를_반환한다(self):
        response = mock.Mock()
        response.json.return_value = [{"market": "KRW-BTC", "trade_price": 90000000}]
        with mock.patch("trading.utils.requests.get", return_value=response):
            self.assertEqual(get_ticker_price("KRW-BTC"), 90000000)

    def test_빈_응답이면_None(self):
        response = mock.Mock()
        response.json.return_value = []
        with mock.patch("trading.utils.requests.get", return_value=response):
            self.assertIsNone(get_ticker_price("KRW-BTC"))

    def test_요청_실패시_None(self):
        with mock.patch("trading.utils.requests.get",
                        side_effect=requests.RequestException("timeout")):
            self.assertIsNone(get_ticker_price("KRW-BTC"))
