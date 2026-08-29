# trading/views.py
from django.shortcuts import render
from django.http import JsonResponse
from .utils import get_account_info, get_krw_market_coin_info, upbit_order
from .auto_trade import AutoTrader, trade_logs
import threading

# 실행 중인 트레이더. 선언이 없으면 start/stop 에서 NameError 가 난다.
trader = None

def main_view(request):
    """ 메인 페이지 """
    return render(request, "main.html", {
        "account_info": get_account_info(),
        "coin_info_list": get_krw_market_coin_info()
    })

def fetch_account_data(request):
    """ AJAX 요청을 받아 전체 계좌 정보를 반환 """
    return JsonResponse({"account_info": get_account_info()})

def fetch_coin_data(request):
    """ AJAX 요청을 받아 상위 5개 코인 정보를 반환 """
    return JsonResponse({"coin_info_list": get_krw_market_coin_info()})

def fetch_trade_logs(request):
    """ ✅ AJAX 요청을 받아 자동매매 로그 반환 """
    return JsonResponse({"logs": trade_logs})

def start_auto_trading(request):
    """ 자동매매 시작 """
    global trader

    try:
        budget = int(request.GET.get("budget", 10000))
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "budget 값이 올바르지 않습니다"}, status=400)

    if budget <= 0:
        return JsonResponse({"status": "error", "message": "budget 은 0보다 커야 합니다"}, status=400)

    if trader is None or not trader.active:
        trader = AutoTrader(budget)
        # 서버 종료를 막지 않도록 데몬 스레드로 실행
        threading.Thread(target=trader.start_trading, daemon=True).start()
        return JsonResponse({"status": "started"})

    return JsonResponse({"status": "already running"})

def stop_auto_trading(request):
    """ 자동매매 중지 """
    global trader
    if trader and trader.active:
        trader.stop_trading()
        return JsonResponse({"status": "stopped"})

    return JsonResponse({"status": "not running"})