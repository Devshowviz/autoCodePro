# trading/market_analysis.py
"""
매수 종목 선정(§4)과 시장 강도 분석(§5).
"""

from django.utils import timezone

from .models import MarketVolumeRecord
from .utils import get_orderbooks

# --- §4 매수 종목 선정 -------------------------------------------------
RISING_CANDIDATE_COUNT = 10      # 1차: 상승률 상위 N개
BID_ASK_RATIO = 1.5              # 2차: 매수 총잔량 / 매도 총잔량 최소 배수
MAX_SPREAD_RATE = 0.001          # 2차: 스프레드 상한 (0.1%)
VOLUME_CANDIDATE_COUNT = 5       # 3차: 거래대금 상위 N개

# --- §5 시장 강도 분석 -------------------------------------------------
BENCHMARK_MARKETS = ("KRW-BTC", "KRW-ETH")
BENCHMARK_RATE = 0.02            # BTC/ETH 평균 변동률 ±2%
VOLUME_CHANGE_RATE = 0.20        # 전체 거래량 변동률 ±20%
UP_DOWN_RATIO = 0.60             # 상승/하락 코인 비율 60%
VOLUME_RECORD_INTERVAL_HOURS = 24

BULLISH, BEARISH, NEUTRAL = "bullish", "bearish", "neutral"


# ======================================================================
# §4 매수 종목 선정
# ======================================================================

def filter_rising_coins(coin_info_list, limit=RISING_CANDIDATE_COUNT):
    """ 1차 - 전일 대비 상승 종목 중 상승률 상위 N개 """
    rising = [c for c in coin_info_list if (c.get("signed_change_rate") or 0) > 0]
    return sorted(rising, key=lambda c: c["signed_change_rate"], reverse=True)[:limit]


def analyze_orderbook(orderbook):
    """ 호가 하나를 분석해 (매수세 우위 여부, 스프레드) 반환

    조건을 판단할 수 없으면 (False, None).
    """
    if not orderbook:
        return False, None

    try:
        total_bid = float(orderbook["total_bid_size"])
        total_ask = float(orderbook["total_ask_size"])
        units = orderbook["orderbook_units"]
        best_ask = float(units[0]["ask_price"])
        best_bid = float(units[0]["bid_price"])
    except (KeyError, IndexError, TypeError, ValueError):
        return False, None

    if best_bid <= 0 or total_ask <= 0:
        return False, None

    spread = (best_ask - best_bid) / best_bid
    return total_bid > total_ask * BID_ASK_RATIO, spread


def filter_by_orderbook(candidates):
    """ 2차 - 매수세가 우위이고 스프레드가 좁은 종목만 통과 """
    if not candidates:
        return []

    orderbooks = get_orderbooks([c["market"] for c in candidates])

    passed = []
    for coin in candidates:
        has_bid_pressure, spread = analyze_orderbook(orderbooks.get(coin["market"]))
        if has_bid_pressure and spread is not None and spread < MAX_SPREAD_RATE:
            passed.append(coin)
    return passed


def select_buy_target(coin_info_list, excluded_markets=()):
    """ §4 3단계를 거쳐 최종 매수 종목 하나를 반환. 없으면 None """
    candidates = [
        c for c in filter_rising_coins(coin_info_list)
        if c["market"] not in excluded_markets
    ]
    if not candidates:
        return None

    passed = filter_by_orderbook(candidates)
    if not passed:
        return None

    # 3차 - 거래대금 상위 N개 중 (현재가 × 거래대금)이 가장 큰 종목
    top_by_volume = sorted(
        passed, key=lambda c: c["acc_trade_price_24h"], reverse=True
    )[:VOLUME_CANDIDATE_COUNT]

    return max(top_by_volume, key=lambda c: c["trade_price"] * c["acc_trade_price_24h"])


# ======================================================================
# §5 시장 강도 분석
# ======================================================================

def analyze_by_benchmark(coin_info_list):
    """ 5.1 - BTC/ETH 평균 변동률 """
    rates = [
        c["signed_change_rate"] for c in coin_info_list
        if c["market"] in BENCHMARK_MARKETS
    ]
    if not rates:
        return NEUTRAL

    average = sum(rates) / len(rates)
    if average > BENCHMARK_RATE:
        return BULLISH
    if average < -BENCHMARK_RATE:
        return BEARISH
    return NEUTRAL


def record_market_volume(total_volume):
    """ 24시간 주기로 전체 시장 거래량을 기록하고, 직전 기록을 반환 """
    latest = MarketVolumeRecord.objects.order_by("-recorded_at").first()

    should_record = latest is None or (
        timezone.now() - latest.recorded_at
    ).total_seconds() >= VOLUME_RECORD_INTERVAL_HOURS * 3600

    if should_record:
        MarketVolumeRecord.objects.create(total_market_volume=total_volume)

    return latest


def analyze_by_volume(coin_info_list):
    """ 5.2 - 전체 시장 거래량 변화 """
    total_volume = sum(c.get("acc_trade_price_24h") or 0 for c in coin_info_list)
    previous = record_market_volume(total_volume)

    if previous is None or not previous.total_market_volume:
        return NEUTRAL

    change = (total_volume - previous.total_market_volume) / previous.total_market_volume
    if change > VOLUME_CHANGE_RATE:
        return BULLISH
    if change < -VOLUME_CHANGE_RATE:
        return BEARISH
    return NEUTRAL


def analyze_by_up_down_ratio(coin_info_list):
    """ 5.3 - 상승/하락 코인 비율 """
    if not coin_info_list:
        return NEUTRAL

    rising = sum(1 for c in coin_info_list if (c.get("signed_change_rate") or 0) > 0)
    falling = sum(1 for c in coin_info_list if (c.get("signed_change_rate") or 0) < 0)
    total = len(coin_info_list)

    if rising / total > UP_DOWN_RATIO:
        return BULLISH
    if falling / total > UP_DOWN_RATIO:
        return BEARISH
    return NEUTRAL


def analyze_market_state(coin_info_list):
    """ 5.4 - 3개 지표 중 2개 이상이 같은 방향이면 그 방향, 아니면 보합 """
    signals = [
        analyze_by_benchmark(coin_info_list),
        analyze_by_volume(coin_info_list),
        analyze_by_up_down_ratio(coin_info_list),
    ]

    for state in (BULLISH, BEARISH):
        if signals.count(state) >= 2:
            return state, signals
    return NEUTRAL, signals
