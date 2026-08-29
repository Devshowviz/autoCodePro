# trading/urls.py
from django.urls import path

from . import views

urlpatterns = [
    path('', views.main_view, name='main-page'),

    path('auto_trade/start/', views.start_auto_trading, name='start-auto-trade'),
    path('auto_trade/stop/', views.stop_auto_trading, name='stop-auto-trade'),

    path('api/fetch_account_data/', views.fetch_account_data, name='fetch-account-data'),
    path('api/fetch_coin_data/', views.fetch_coin_data, name='fetch-coin-data'),
    path('api/trade_logs/', views.fetch_trade_logs, name='fetch-trade-logs'),
    path('api/check_auto_trading/', views.check_auto_trading, name='check-auto-trading'),
    path('api/get_market_volume/', views.get_market_volume, name='get-market-volume'),
    path('api/getRecntTradeLog/', views.recent_trade_log, name='recent-trade-log'),
    path('api/recentProfitLog/', views.recent_profit_log, name='recent-profit-log'),

    # 이전 경로 호환
    path('api/account_data/', views.fetch_account_data, name='fetch-account-data-legacy'),
    path('api/coin_data/', views.fetch_coin_data, name='fetch-coin-data-legacy'),
]
