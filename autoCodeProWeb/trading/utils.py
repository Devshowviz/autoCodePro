# trading/utils.py
import hashlib
import time
import uuid
from urllib.parse import urlencode

import jwt
import pandas as pd
import requests
from django.conf import settings

UPBIT_API_URL = "https://api.upbit.com"
REQUEST_TIMEOUT = 5  # 초

# 업비트 주문 API 의 side 값 (bid: 매수, ask: 매도)
ORDER_SIDES = {"buy": "bid", "sell": "ask"}

# 호가는 한 번에 여러 종목을 조회할 수 있으나, 429 를 피하려고 나눠 보낸다.
ORDERBOOK_BATCH_SIZE = 10
ORDERBOOK_CACHE_TTL = 5  # 초

# {market: (조회시각, 호가 dict)}
_orderbook_cache = {}


def _safe_json(response):
    """ 응답 본문을 JSON 으로 파싱. 실패하면 상태 코드를 담은 dict 반환 """
    try:
        return response.json()
    except ValueError:
        return {"message": f"응답을 JSON 으로 파싱할 수 없음 (status={response.status_code})"}


def _auth_headers(params=None):
    """ 업비트 인증 헤더 생성

    JWT payload 에는 access_key 와 nonce 가 반드시 들어가야 하고,
    파라미터가 있는 요청은 query_hash 까지 함께 서명해야 인증을 통과한다.
    """
    payload = {
        "access_key": settings.UPBIT_ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
    }

    if params:
        query = urlencode(params)
        payload["query_hash"] = hashlib.sha512(query.encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"

    jwt_token = jwt.encode(payload, settings.UPBIT_SECRET_KEY, algorithm="HS256")
    return {"Authorization": f"Bearer {jwt_token}"}


# ----------------------------------------------------------------------
# 계좌
# ----------------------------------------------------------------------

def get_account_info():
    """ 업비트 전체 계좌 조회 API 호출 """
    try:
        response = requests.get(
            f"{UPBIT_API_URL}/v1/accounts",
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        return {"error": {"message": f"계좌 조회 요청 실패: {e}"}}

    if response.status_code != 200:
        return {"error": _safe_json(response)}

    return _safe_json(response)


def get_balance(currency):
    """ 특정 화폐(KRW, BTC 등)의 보유 수량 조회. 조회 실패 시 0.0 """
    accounts = get_account_info()

    # 오류 응답은 list 가 아닌 dict 로 돌아온다
    if not isinstance(accounts, list):
        return 0.0

    for account in accounts:
        if account.get("currency") == currency:
            try:
                return float(account.get("balance", 0))
            except (TypeError, ValueError):
                return 0.0

    return 0.0


def get_held_currencies(accounts=None):
    """ 실제로 보유 중인 코인 화폐 코드 집합 (KRW 제외)

    사용자가 업비트 앱에서 직접 매도한 경우를 감지하는 데 쓴다.
    """
    if accounts is None:
        accounts = get_account_info()
    if not isinstance(accounts, list):
        return None  # 조회 실패는 "보유 없음" 과 구분해야 한다

    held = set()
    for account in accounts:
        currency = account.get("currency")
        if currency == "KRW":
            continue
        try:
            if float(account.get("balance", 0)) > 0:
                held.add(currency)
        except (TypeError, ValueError):
            continue
    return held


# ----------------------------------------------------------------------
# 시세
# ----------------------------------------------------------------------

def get_krw_market_coin_info():
    """ KRW 마켓 전 종목 시세를 24시간 거래대금 내림차순으로 반환. 실패 시 빈 리스트 """
    try:
        markets_response = requests.get(
            f"{UPBIT_API_URL}/v1/market/all", timeout=REQUEST_TIMEOUT
        ).json()
        krw_markets = [
            m["market"] for m in markets_response if m["market"].startswith("KRW-")
        ]

        ticker_response = requests.get(
            f"{UPBIT_API_URL}/v1/ticker",
            params={"markets": ",".join(krw_markets)},
            timeout=REQUEST_TIMEOUT,
        ).json()
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return []

    if not isinstance(ticker_response, list):
        return []

    coin_info_list = [{
        "market": ticker["market"],
        "trade_price": ticker["trade_price"],
        "high_price": ticker.get("high_price"),
        "low_price": ticker.get("low_price"),
        "trade_volume": ticker.get("trade_volume"),
        "signed_change_rate": ticker["signed_change_rate"],
        "acc_trade_price_24h": ticker["acc_trade_price_24h"],
        "acc_trade_volume_24h": ticker.get("acc_trade_volume_24h"),
    } for ticker in ticker_response]

    return sorted(coin_info_list, key=lambda x: x["acc_trade_price_24h"], reverse=True)


def get_top_coin_info(limit=5):
    """ 대시보드 표시용 상위 종목 """
    return get_krw_market_coin_info()[:limit]


def get_ticker_price(market):
    """ 특정 마켓의 현재가 조회. 실패 시 None """
    try:
        response = requests.get(
            f"{UPBIT_API_URL}/v1/ticker",
            params={"markets": market},
            timeout=REQUEST_TIMEOUT,
        )
        tickers = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(tickers, list) or not tickers:
        return None

    return tickers[0].get("trade_price")


def get_orderbooks(markets):
    """ 여러 종목의 호가를 배치로 조회. {market: 호가} 반환

    5초 캐시를 두어 같은 종목을 반복 조회하지 않는다 (429 방지).
    """
    now = time.time()
    result = {}
    to_fetch = []

    for market in markets:
        cached = _orderbook_cache.get(market)
        if cached and now - cached[0] < ORDERBOOK_CACHE_TTL:
            result[market] = cached[1]
        else:
            to_fetch.append(market)

    for start in range(0, len(to_fetch), ORDERBOOK_BATCH_SIZE):
        batch = to_fetch[start:start + ORDERBOOK_BATCH_SIZE]
        try:
            response = requests.get(
                f"{UPBIT_API_URL}/v1/orderbook",
                params={"markets": ",".join(batch)},
                timeout=REQUEST_TIMEOUT,
            )
            orderbooks = response.json()
        except (requests.RequestException, ValueError):
            continue

        if not isinstance(orderbooks, list):
            continue

        for orderbook in orderbooks:
            market = orderbook.get("market")
            if not market:
                continue
            _orderbook_cache[market] = (now, orderbook)
            result[market] = orderbook

    return result


def get_candles(market, count=200):
    """ 초봉 데이터를 DataFrame(close/high/low)으로 반환. 실패 시 None """
    try:
        response = requests.get(
            f"{UPBIT_API_URL}/v1/candles/seconds",
            params={"market": market, "count": count},
            timeout=REQUEST_TIMEOUT,
        )
        candles = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(candles, list) or not candles:
        return None

    # 업비트는 최신 캔들부터 반환하므로 시간 순서대로 뒤집는다
    candles = list(reversed(candles))

    try:
        return pd.DataFrame({
            "close": [float(c["trade_price"]) for c in candles],
            "high": [float(c["high_price"]) for c in candles],
            "low": [float(c["low_price"]) for c in candles],
        })
    except (KeyError, TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# 주문
# ----------------------------------------------------------------------

def upbit_order(market, side, volume=None, price=None, ord_type="limit"):
    """ 업비트 주문 API 호출 (매수/매도)

    side     : "buy"(매수) / "sell"(매도)
    ord_type : "price"  시장가 매수 - price 필요
               "market" 시장가 매도 - volume 필요
               "limit"  지정가     - volume, price 모두 필요
    """
    if side not in ORDER_SIDES:
        return {"error": {"message": f"알 수 없는 side 값: {side}"}}

    if ord_type == "price":
        required = {"price": price}
    elif ord_type == "market":
        required = {"volume": volume}
    elif ord_type == "limit":
        required = {"volume": volume, "price": price}
    else:
        return {"error": {"message": f"알 수 없는 ord_type 값: {ord_type}"}}

    missing = [name for name, value in required.items() if value is None]
    if missing:
        return {"error": {"message": f"{ord_type} 주문에 {', '.join(missing)} 값이 필요합니다"}}

    params = {
        "market": market,
        "side": ORDER_SIDES[side],
        "ord_type": ord_type,
    }
    params.update({name: str(value) for name, value in required.items()})

    try:
        response = requests.post(
            f"{UPBIT_API_URL}/v1/orders",
            headers=_auth_headers(params),
            json=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        return {"error": {"message": f"주문 요청 실패: {e}"}}

    if response.status_code not in (200, 201):
        return {"error": _safe_json(response)}

    return _safe_json(response)


def get_order(order_uuid):
    """ 주문 UUID 로 주문 상태 조회 """
    params = {"uuid": order_uuid}

    try:
        response = requests.get(
            f"{UPBIT_API_URL}/v1/order",
            params=params,
            headers=_auth_headers(params),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        return {"error": {"message": f"주문 조회 실패: {e}"}}

    if response.status_code != 200:
        return {"error": _safe_json(response)}

    return _safe_json(response)


def is_order_done(order_uuid):
    """ 주문이 체결 완료(state=done)되었는지 확인 """
    if not order_uuid:
        return False
    order = get_order(order_uuid)
    return isinstance(order, dict) and order.get("state") == "done"
