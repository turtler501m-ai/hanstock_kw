import json

from src import trader
from src.dashboard.services.balance_service import to_int as _to_int
from src.market_metadata import normalize_kr_order_symbol


def _broker_order_id_from_history(row: dict) -> str:
    for key in ("ODNO", "odno", "ord_no", "order_no"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


def _history_int(row: dict, *keys: str) -> int:
    for key in keys:
        value = row.get(key)
        parsed = _to_int(value)
        if parsed:
            return parsed
    return 0


def _history_fill_price(row: dict) -> int:
    return _history_int(
        row,
        "avg_prvs",
        "avg_pric",
        "avg_ccld_pric",
        "avg_prc",
        "cntr_uv",
        "cntr_pric",
        "ccld_unpr",
        "ord_unpr",
    )


def _history_fill_qty(row: dict) -> int:
    for key in ("tot_ccld_qty", "ccld_qty", "cnqn", "cntr_qty", "tot_cntr_qty", "filled_qty"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return _to_int(value)
    return 0


def _history_requested_qty(row: dict) -> int:
    return _history_int(row, "ord_qty", "requested_qty") or _history_fill_qty(row)


def _history_remaining_qty(row: dict) -> int:
    explicit = _history_int(
        row, "ord_remnq", "rmn_qty", "RMN_QTY", "ord_psbl_qty", "oso_qty", "remaining_qty"
    )
    if explicit:
        return explicit
    return max(0, _history_requested_qty(row) - _history_fill_qty(row))


def _history_order_is_canceled(row: dict) -> bool:
    value = _history_text(
        row,
        "cncl_yn",
        "CNCL_YN",
        "rvse_cncl_dvsn_name",
        "RVSE_CNCL_DVSN_NAME",
        "mdfy_cncl",
        "MDFY_CNCL",
        "canceled",
        "cancel_yn",
    ).strip()
    return value.upper() == "Y" or "취소" in value or "cancel" in value.lower()
def _history_original_order_id(row: dict) -> str:
    return _history_text(
        row,
        "orig_ord_no", "orig_odno", "ORIG_ORD_NO", "ORIG_ODNO",
        "orgn_ord_no", "original_order_no", "ori_ord", "ORI_ORD",
    )


def _normalize_history_cancellations(history: list[dict]) -> list[dict]:
    """Fold Kiwoom's separate cancellation rows into their original orders."""
    canceled_original_ids = {
        _history_original_order_id(row)
        for row in history
        if _history_original_order_id(row) and _history_order_is_canceled(row)
    }
    if not canceled_original_ids:
        return history

    normalized = []
    for row in history:
        original_order_id = _history_original_order_id(row)
        if original_order_id and _history_order_is_canceled(row):
            continue
        order_id = _broker_order_id_from_history(row)
        if order_id in canceled_original_ids and not _history_order_is_canceled(row):
            row = {**row, "cncl_yn": "Y"}
        normalized.append(row)
    return normalized


def _history_order_is_rejected(row: dict) -> bool:
    return _history_int(row, "rjct_qty", "RJCT_QTY") > 0


def _history_text(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _history_symbol(row: dict) -> str:
    symbol = normalize_kr_order_symbol(_history_text(
        row, "pdno", "PDNO", "isu_no", "mksc_shrn_iscd", "stk_cd", "symbol"
    ))
    return symbol[1:] if len(symbol) == 7 and symbol[:1].upper() == "A" and symbol[1:].isdigit() else symbol


def _history_name(row: dict) -> str:
    return _history_text(
        row, "prdt_name", "PRDT_NAME", "itm_name", "item_name", "stk_nm", "stock_name"
    ) or _history_symbol(row)


def _history_action(row: dict) -> str:
    code = _history_text(row, "sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD", "trad_dvsn_cd", "sell_tp")
    if code == "01":
        return "sell"
    if code == "02":
        return "buy"

    label = _history_text(
        row, "sll_buy_dvsn_name", "trad_dvsn_name", "buy_sell_name", "io_tp_nm", "io_tp", "side"
    ).lower()
    if "sell" in label or "매도" in label:
        return "sell"
    if "buy" in label or "매수" in label:
        return "buy"
    return ""


def _history_timestamp(row: dict) -> str:
    raw_date = _history_text(row, "ord_dt", "ORD_DT", "ccld_dt", "CCLD_DT", "trad_dt")
    raw_time = _history_text(
        row, "ord_tmd", "ORD_TMD", "ord_tm", "ccld_tmd", "CCLD_TMD", "cntr_tm", "trad_tmd"
    )
    digits_date = "".join(char for char in raw_date if char.isdigit())
    digits_time = "".join(char for char in raw_time if char.isdigit())
    if len(digits_date) >= 8:
        date_text = f"{digits_date[:4]}-{digits_date[4:6]}-{digits_date[6:8]}"
    else:
        date_text = trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
    if len(digits_time) >= 6:
        time_text = f"{digits_time[:2]}:{digits_time[2:4]}:{digits_time[4:6]}"
    else:
        time_text = "00:00:00"
    return f"{date_text} {time_text}"


def _history_trade_key(trade: dict) -> tuple:
    order_id = str(trade.get("broker_order_id") or "").strip()
    if order_id:
        ts = str(trade.get("ts") or trade.get("timestamp") or "")
        trade_date = ts[:10] if len(ts) >= 10 else ""
        return (
            "order",
            order_id,
            trade_date,
            str(trade.get("symbol") or ""),
            str(trade.get("action") or ""),
        )
    return (
        "trade",
        str(trade.get("ts") or trade.get("timestamp") or ""),
        str(trade.get("symbol") or ""),
        str(trade.get("action") or ""),
        _to_int(trade.get("qty")),
        _to_int(trade.get("price")),
    )


def _history_matches_tracked_order(row: dict, trade: dict) -> bool:
    if _broker_order_id_from_history(row) != str(trade.get("broker_order_id") or "").strip():
        return False
    row_symbol = normalize_kr_order_symbol(_history_symbol(row))
    trade_symbol = normalize_kr_order_symbol(trade.get("symbol"))
    if row_symbol and trade_symbol and row_symbol != trade_symbol:
        return False
    row_action = _history_action(row)
    trade_action = str(trade.get("action") or "").lower()
    if row_action and trade_action and row_action != trade_action:
        return False
    row_date = _history_timestamp(row)[:10]
    trade_ts = str(trade.get("ts") or trade.get("timestamp") or "")
    trade_date = trade_ts[:10] if len(trade_ts) >= 10 else ""
    if row_date and trade_date and row_date != trade_date:
        return False
    return True


def _history_row_to_trade(row: dict) -> dict:
    symbol = _history_symbol(row)
    action = _history_action(row)
    requested_qty = _history_requested_qty(row)
    filled_qty = _history_fill_qty(row)
    remaining_qty = _history_remaining_qty(row)
    price = _history_fill_price(row)
    if not symbol or action not in {"buy", "sell"} or requested_qty <= 0:
        return {}
    if _history_order_is_canceled(row):
        order_status = "canceled"
    elif _history_order_is_rejected(row) and filled_qty <= 0:
        order_status = "failed"
    elif remaining_qty > 0 and filled_qty <= 0:
        order_status = "open"
    elif remaining_qty > 0 or filled_qty < requested_qty:
        order_status = "partial"
    else:
        order_status = "filled"
    return {
        "ts": _history_timestamp(row),
        "symbol": symbol,
        "name": _history_name(row),
        "action": action,
        "qty": requested_qty,
        "price": price,
        "reason": "broker history import",
        "ok": int(order_status != "failed"),
        "env": trader.runtime_flags().trading_env,
        "dry_run": 0,
        "broker_order_id": _broker_order_id_from_history(row),
        "order_status": order_status,
        "filled_qty": filled_qty,
        "filled_price": price,
        "response_msg": "Kiwoom trade history import",
        "broker_result": json.dumps(row, ensure_ascii=False),
    }
