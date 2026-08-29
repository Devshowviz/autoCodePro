# trading/utils.py
import hashlib
import uuid
from urllib.parse import urlencode

import jwt
import requests
from django.conf import settings

UPBIT_API_URL = "https://api.upbit.com"
REQUEST_TIMEOUT = 5  # 초

# 업비트 주문 API 의 side 값 (bid: 매수, ask: 매도)
ORDER_SIDES = {"buy": "bid", "sell": "ask"}


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


def get_ticker_price(market):
    """ 특정 마켓의 현재가 조회. 실패 시 None

    보유 종목은 거래대금 상위 5개 밖으로 밀려날 수 있으므로,
    목록에 의존하지 않고 해당 마켓을 직접 조회한다.
    """
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


def get_krw_market_coin_info():
    """ KRW 마켓에서 거래대금 상위 5개 코인 조회. 실패 시 빈 리스트 """
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

    coin_info_list = [{
        "market": ticker["market"],
        "trade_price": ticker["trade_price"],
        "signed_change_rate": ticker["signed_change_rate"],
        "acc_trade_price_24h": ticker["acc_trade_price_24h"],
    } for ticker in ticker_response]

    return sorted(
        coin_info_list,
        key=lambda x: (x["acc_trade_price_24h"], x["signed_change_rate"]),
        reverse=True,
    )[:5]


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
