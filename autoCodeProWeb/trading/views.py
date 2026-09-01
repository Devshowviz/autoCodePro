# trading/views.py
import threading

from django.http import JsonResponse
from django.shortcuts import render

from .auto_trade import (
    LOSS_CUT_RATE,
    AutoTrader,
    profit_logs,
    today_kst,
    trade_logs,
)
from .market_analysis import analyze_market_state
from .models import DailyPnlRecord, TradeRecord
from .utils import get_account_info, get_krw_market_coin_info, get_top_coin_info

# 실행 중인 트레이더. 선언이 없으면 start/stop 에서 NameError 가 난다.
trader = None
trader_thread = None


def main_view(request):
    """ 메인 페이지 """
    return render(request, "main.html", {
        "account_info": get_account_info(),
        "coin_info_list": get_top_coin_info(),
    })


def fetch_account_data(request):
    """ AJAX 요청을 받아 전체 계좌 정보를 반환 """
    return JsonResponse({"account_info": get_account_info()})


def fetch_coin_data(request):
    """ AJAX 요청을 받아 상위 5개 코인 정보를 반환 """
    return JsonResponse({"coin_info_list": get_top_coin_info()})


def fetch_trade_logs(request):
    """ ✅ AJAX 요청을 받아 자동매매 로그 반환 """
    # 매매 스레드가 동시에 수정할 수 있으므로 사본을 직렬화한다
    return JsonResponse({"logs": trade_logs[:]})


def check_auto_trading(request):
    """ 자동매매 실행 여부와 보유 종목 반환 """
    running = trader is not None and trader.active

    if trader is not None:
        daily_pnl = trader.daily_pnl
        run_pnl = trader.run_pnl()
        daily_loss_limit = trader.daily_loss_limit()
        loss_cut = trader.loss_cut_reached()
    else:
        # 서버를 재시작해 trader 가 없어도 당일 손익·기준 총자산은 DB 에 남아
        # 있다. 여기서 0 을 반환하면 화면이 실제 손익을 감춘다.
        record = DailyPnlRecord.objects.filter(date=today_kst()).first()
        daily_pnl = record.realized_pnl if record else 0.0
        run_pnl = 0.0
        daily_loss_limit = (record.equity_base if record else 0.0) * LOSS_CUT_RATE
        loss_cut = False

    return JsonResponse({
        "running": running,
        "positions": trader.positions_snapshot() if trader else [],
        "daily_pnl": round(daily_pnl),
        "run_pnl": round(run_pnl),
        "daily_loss_limit": round(daily_loss_limit),
        "loss_cut": loss_cut,
    })


def get_market_volume(request):
    """ 현재 시장 상태 반환 """
    coin_info_list = get_krw_market_coin_info()
    if not coin_info_list:
        return JsonResponse({"state": "unknown", "signals": []})

    state, signals = analyze_market_state(coin_info_list)
    return JsonResponse({
        "state": state,
        "signals": signals,
        "total_volume": sum(c.get("acc_trade_price_24h") or 0 for c in coin_info_list),
    })


def recent_trade_log(request):
    """ 최근 매도 체결 내역 (거래 기록 기준) """
    records = TradeRecord.objects.filter(is_active=False).order_by("-created_at")[:20]
    return JsonResponse({"records": [{
        "market": r.market,
        "buy_price": r.buy_price,
        "highest_price": r.highest_price,
        "buy_krw_price": r.buy_krw_price,
        "created_at": r.created_at.isoformat(),
    } for r in records]})


def recent_profit_log(request):
    """ 최근 수익 로그 (매수가/매도가/수익률) """
    return JsonResponse({"profits": profit_logs[::-1]})


def start_auto_trading(request):
    """ 자동매매 시작 """
    global trader, trader_thread

    try:
        budget = int(request.GET.get("budget", 10000))
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "budget 값이 올바르지 않습니다"}, status=400)

    if budget <= 0:
        return JsonResponse({"status": "error", "message": "budget 은 0보다 커야 합니다"}, status=400)

    if trader is None or not trader.active:
        # 기준 총자산을 잡기 전에는 전역에 넣지 않는다. 조회에 실패한 요청이
        # 대시보드 표시에 섞이지 않도록 한다.
        candidate = AutoTrader(budget)

        # 손실 한도는 '실행 시작 시점 총자산' 기준이라, 시작할 때마다 새로
        # 잡는다. 로스컷으로 정지한 뒤에도 이 값으로 다시 시작할 수 있다.
        candidate.begin_run()
        if candidate.equity_base <= 0:
            return JsonResponse({
                "status": "error",
                "message": (
                    "총자산을 조회하지 못해 손실 한도를 정할 수 없습니다. "
                    "업비트 API 키와 네트워크를 확인한 뒤 다시 시도해 주세요."
                ),
            }, status=503)

        trader = candidate
        # 서버 종료를 막지 않도록 데몬 스레드로 실행
        trader_thread = threading.Thread(target=trader.start_trading, daemon=True)
        trader_thread.start()
        return JsonResponse({"status": "started"})

    return JsonResponse({"status": "already running"})


def stop_auto_trading(request):
    """ 자동매매 중지 """
    global trader, trader_thread

    if trader and trader.active:
        trader.stop_trading()
        # 스레드가 실제로 끝날 때까지 잠시 기다린다
        if trader_thread and trader_thread.is_alive():
            trader_thread.join(timeout=5)
        return JsonResponse({"status": "stopped"})

    return JsonResponse({"status": "not running"})
