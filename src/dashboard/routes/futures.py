# -*- coding: utf-8 -*-
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse
import src.dashboard.core as _core
from src.dashboard.core import *
globals().update({k: v for k, v in _core.__dict__.items() if not k.startswith('__')})

router = APIRouter(tags=["futures"])

@router.get("/api/futures-signals/summary")
def get_futures_signals_summary():
    db_signals = _list_db_futures_signals(limit=500)
    if db_signals:
        return _futures_signals_summary(db_signals, telegram_connected=True)
    records = _get_futures_signal_service().list_records(limit=None)
    return _futures_signals_summary(records)




@router.get("/api/futures-signals")
def get_futures_signals(limit: int = 100):
    safe_limit = max(1, min(int(limit or 100), 500))
    db_signals = _list_db_futures_signals(limit=safe_limit)
    if db_signals:
        return {"signals": db_signals}
    records = _get_futures_signal_service().list_records(limit=safe_limit)
    return {"signals": [_futures_signal_record_to_api(record) for record in records]}




@router.get("/api/futures-signals/collector/status")
def get_futures_signal_collector_status():
    status = collector_status()
    # `connected`: session이 있고 ready이면 연결된 것으로 간주
    status.setdefault("connected", bool(status.get("ready") and status.get("session_available")))
    # `running`: poll.py 프로세스가 실제로 실행 중인지 확인
    status["running"] = _is_poll_running()
    return status




@router.post("/api/futures-signals/parse")
def parse_futures_signal_message(payload: dict = Body(...)):
    text = str(payload.get("text") or payload.get("raw_text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    source = str(payload.get("source") or "telegram_manual")
    source_message_id = payload.get("source_message_id")
    received_at = payload.get("received_at")
    parsed_received_at = None
    if received_at:
        try:
            parsed_received_at = trader.datetime.fromisoformat(str(received_at))
        except ValueError:
            raise HTTPException(status_code=400, detail="received_at must be ISO-8601")
    try:
        record = _get_futures_signal_service().ingest_message(
            text,
            source=source,
            source_message_id=str(source_message_id) if source_message_id else None,
            received_at=parsed_received_at,
        )
    except FuturesSignalParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"signal": _futures_signal_record_to_api(record)}




@router.post("/api/futures-signals/{signal_id}/verify")
def verify_futures_signal(signal_id: str, payload: dict = Body(...)):
    record = _find_futures_signal_record(signal_id)
    if record is None:
        raise HTTPException(status_code=404, detail="futures signal not found")

    candles_payload = payload.get("candles") or []
    if not isinstance(candles_payload, list) or not candles_payload:
        raise HTTPException(status_code=400, detail="candles list is required")

    candles = []
    for item in candles_payload:
        try:
            candles.append(
                OhlcCandle(
                    timestamp=item.get("timestamp") or item.get("time") or "",
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="each candle must include open/high/low/close")

    updated = _get_futures_signal_service().verify(record.signal.id, candles)
    if updated is None:
        raise HTTPException(status_code=404, detail="futures signal not found")
    return {"signal": _futures_signal_record_to_api(updated)}



@router.get("/api/futures/balance")
def get_futures_balance():
    try:
        api = _get_futures_api()
        result = api.get_balance()
        if result.get("rt_cd") != "0":
            return {"error": result.get("msg1", "failed"), "data": None}
        output = result.get("output", {})
        return {
            "raw": output,
            "cash": output.get("mnres_krw") or output.get("frcr_dncl_amt") or output.get("frcr_buy_amt_smtl") or "미확인",
            "total_assets": output.get("tot_asst") or output.get("evlu_amt_smtl") or "미확인",
            "margin": output.get("mgn") or "미확인",
            "orderable_cash": output.get("ord_cash") or "미확인",
            "status": "ok" if output else "no_data",
        }
    except Exception as e:
        return {"error": str(e), "data": None}




@router.get("/api/futures/positions")
def get_futures_positions():
    try:
        api = _get_futures_api()
        result = api.get_positions()
        if result.get("rt_cd") != "0":
            return {"error": result.get("msg1", "failed"), "positions": []}
        output = result.get("output", [])
        if not isinstance(output, list):
            output = [output] if output else []
        return {"positions": output}
    except Exception as e:
        return {"error": str(e), "positions": []}




@router.get("/api/futures/orders")
def get_futures_orders(start_date: str = None, end_date: str = None):
    try:
        api = _get_futures_api()
        result = api.get_daily_orders(start_date, end_date)
        if result.get("rt_cd") != "0":
            return {"error": result.get("msg1", "failed"), "orders": []}
        output = result.get("output1", [])
        if not isinstance(output, list):
            output = [output] if output else []
        return {"orders": output}
    except Exception as e:
        return {"error": str(e), "orders": []}




@router.get("/api/futures/quote/{symbol}")
def get_futures_quote(symbol: str):
    try:
        api = _get_futures_api()
        result = api.get_current_price(symbol)
        if result.get("rt_cd") != "0":
            return {"error": result.get("msg1", "failed"), "quote": None}
        return {"quote": result.get("output1", {})}
    except Exception as e:
        return {"error": str(e), "quote": None}




@router.post("/api/futures/order")
def place_futures_order(payload: dict = Body(...)):
    try:
        api = _get_futures_api()
        symbol = payload.get("symbol")
        order_type = payload.get("type")
        qty = payload.get("qty")
        price = payload.get("price", "0")
        if not all([symbol, order_type, qty]):
            raise HTTPException(status_code=400, detail="symbol, type, qty required")
        result = api.place_order(symbol, order_type, int(qty), price)
        if result.get("rt_cd") != "0":
            return {"error": result.get("msg1", "order failed"), "success": False}
        return {"success": True, "order_no": result.get("output", {}).get("ODNO", "")}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e), "success": False}




@router.post("/api/futures/order/cancel")
def cancel_futures_order(payload: dict = Body(...)):
    try:
        api = _get_futures_api()
        order_no = payload.get("order_no")
        order_date = payload.get("order_date", "")
        if not order_no:
            raise HTTPException(status_code=400, detail="order_no required")
        result = api.cancel_order(order_no, order_date)
        if result.get("rt_cd") != "0":
            return {"error": result.get("msg1", "cancel failed"), "success": False}
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e), "success": False}


