from django.contrib import admin

from .models import AskRecord, FailedMarket, MarketVolumeRecord, TradeRecord


@admin.register(TradeRecord)
class TradeRecordAdmin(admin.ModelAdmin):
    list_display = ("market", "buy_price", "highest_price", "buy_krw_price", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("market", "uuid")


@admin.register(FailedMarket)
class FailedMarketAdmin(admin.ModelAdmin):
    list_display = ("market", "failed_at")
    search_fields = ("market",)


@admin.register(MarketVolumeRecord)
class MarketVolumeRecordAdmin(admin.ModelAdmin):
    list_display = ("recorded_at", "total_market_volume")


@admin.register(AskRecord)
class AskRecordAdmin(admin.ModelAdmin):
    list_display = ("market", "recorded_at")
    search_fields = ("market",)
