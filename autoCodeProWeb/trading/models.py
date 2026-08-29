from django.db import models


class TradeRecord(models.Model):
    """ 매수한 거래 기록. 재시작 시 활성 거래 복원에 사용한다. """

    market = models.CharField(max_length=20, unique=True)      # 종목 코드 (예: KRW-BTC)
    buy_price = models.FloatField()                            # 매수 가격
    highest_price = models.FloatField(default=0)               # 보유 중 최고가 (트레일링 스탑용)
    uuid = models.CharField(max_length=100, unique=True, null=True, blank=True)  # 주문 UUID
    created_at = models.DateTimeField(auto_now_add=True)       # 매수 시점 (보유 시간 계산용)
    is_active = models.BooleanField(default=True)              # 거래 활성 상태
    buy_krw_price = models.FloatField(default=0)               # 매수에 사용한 원화 금액

    class Meta:
        verbose_name = "거래 기록"
        verbose_name_plural = "거래 기록"

    def __str__(self):
        state = "보유중" if self.is_active else "종료"
        return f"{self.market} @ {self.buy_price} ({state})"


class FailedMarket(models.Model):
    """ 주문에 실패한 종목. 이후 매수 대상에서 제외한다. """

    market = models.CharField(max_length=20, unique=True)
    failed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "주문 실패 종목"
        verbose_name_plural = "주문 실패 종목"

    def __str__(self):
        return self.market


class MarketVolumeRecord(models.Model):
    """ 전체 시장 거래량 스냅샷. 24시간 전과 비교해 시장 강도를 판단한다. """

    recorded_at = models.DateTimeField(auto_now_add=True)
    total_market_volume = models.FloatField()

    class Meta:
        verbose_name = "시장 거래량 기록"
        verbose_name_plural = "시장 거래량 기록"

    def __str__(self):
        return f"{self.recorded_at:%Y-%m-%d %H:%M} / {self.total_market_volume}"


class AskRecord(models.Model):
    """ 매도한 종목 기록. 일정 시간 동안 같은 종목 재매수를 막는다. """

    market = models.CharField(max_length=20, unique=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "매도 기록"
        verbose_name_plural = "매도 기록"

    def __str__(self):
        return f"{self.market} @ {self.recorded_at:%Y-%m-%d %H:%M}"
