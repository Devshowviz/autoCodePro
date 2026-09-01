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


class DailyPnlRecord(models.Model):
    """ 당일 누적 실현손익. 서버 재시작 후에도 일일 손실 한도가 유지되도록 DB 에 기록한다. """

    date = models.DateField(unique=True)          # KST 기준 날짜
    realized_pnl = models.FloatField(default=0)   # 당일 누적 실현손익(원)

    # 손실 한도의 기준 총자산(원). 자동매매를 시작한 시점의 KRW 잔고와 보유 코인
    # 평가액을 더한 값이며, 시작할 때마다 그 시점 값으로 다시 잡는다.
    equity_base = models.FloatField(default=0)

    class Meta:
        verbose_name = "일일 실현손익"
        verbose_name_plural = "일일 실현손익"

    def __str__(self):
        return f"{self.date} / {self.realized_pnl:+,.0f}원"
