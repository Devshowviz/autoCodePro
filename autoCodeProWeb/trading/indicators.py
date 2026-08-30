# trading/indicators.py
"""
캔들 데이터(pandas DataFrame)를 입력받아 기술적 지표를 계산한다.

각 함수는 최신 값(iloc[-1])을 반환하며, 데이터가 부족하면 None 을 돌려준다.
입력 DataFrame 은 close / high / low 컬럼을 가진다.
"""

import pandas as pd

RSI_PERIOD = 14
MACD_SHORT, MACD_LONG, MACD_SIGNAL = 12, 26, 9
STOCHASTIC_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
ATR_PERIOD = 14


def _last(series):
    """ Series 의 마지막 값을 float 로 반환. 비었거나 NaN 이면 None """
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)


def calculate_rsi(close, period=RSI_PERIOD):
    """ 상대강도지수(RSI). Wilder 평활 방식

    첫 구간은 단순평균으로 시드한 뒤 평활한다. pandas 의
    ewm(adjust=False) 는 첫 값 하나로 시드해 Wilder 정의와 어긋나므로
    직접 계산한다.
    """
    if close is None or len(close) < period + 1:
        return None

    delta = close.diff().dropna()
    gains = delta.clip(lower=0).tolist()
    losses = (-delta.clip(upper=0)).tolist()

    if len(gains) < period:
        return None

    last_gain = sum(gains[:period]) / period
    last_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        last_gain = (last_gain * (period - 1) + gain) / period
        last_loss = (last_loss * (period - 1) + loss) / period

    if last_loss == 0:
        # 하락이 전혀 없으면 100, 상승도 없으면 중립 50
        return 100.0 if last_gain > 0 else 50.0

    rs = last_gain / last_loss
    return 100 - (100 / (1 + rs))


def calculate_ema(close, period):
    """ 지수이동평균(EMA) """
    if close is None or len(close) < period:
        return None
    return _last(close.ewm(span=period, adjust=False).mean())


def calculate_macd(close, short=MACD_SHORT, long=MACD_LONG, signal=MACD_SIGNAL):
    """ MACD. (macd, signal, histogram) 튜플 반환 """
    if close is None or len(close) < long:
        return None

    ema_short = close.ewm(span=short, adjust=False).mean()
    ema_long = close.ewm(span=long, adjust=False).mean()
    macd_line = ema_short - ema_long
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()

    macd_value, signal_value = _last(macd_line), _last(signal_line)
    if macd_value is None or signal_value is None:
        return None

    return macd_value, signal_value, macd_value - signal_value


def calculate_stochastic(close, high, low, period=STOCHASTIC_PERIOD):
    """ 스토캐스틱. (%K, %D) 튜플 반환 """
    if close is None or len(close) < period:
        return None

    lowest = low.rolling(window=period).min()
    highest = high.rolling(window=period).max()
    span = highest - lowest

    # 고가와 저가가 같은 구간은 0 으로 나누게 되므로 중립값 50 으로 둔다
    k = ((close - lowest) / span.replace(0, float("nan")) * 100).fillna(50)
    d = k.rolling(window=3).mean()

    k_value, d_value = _last(k), _last(d)
    if k_value is None:
        return None

    return k_value, d_value if d_value is not None else k_value


def calculate_bollinger_bands(close, period=BOLLINGER_PERIOD, num_std=BOLLINGER_STD):
    """ 볼린저 밴드. (상단, 중간, 하단) 튜플 반환 """
    if close is None or len(close) < period:
        return None

    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    upper, center, lower = _last(middle + std * num_std), _last(middle), _last(middle - std * num_std)
    if center is None:
        return None

    return upper, center, lower


def calculate_atr(close, high, low, period=ATR_PERIOD):
    """ 평균진폭(ATR). 변동성 크기 측정 """
    if close is None or len(close) < period + 1:
        return None

    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)

    return _last(true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean())
