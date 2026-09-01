"""Kiwoom US-stock adapter for the legacy mistock service."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Mapping

from src.broker.kiwoom_client import KiwoomRestClient


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


class KiwoomUSStockAdapter:
    """Expose Kiwoom US account and demo-order APIs in Mistock's legacy shape."""

    broker_name = "kiwoom"

    def __init__(self, client: KiwoomRestClient, *, account_no: str = "", order_submission_enabled: bool = False, default_exchange: str = "ND", exchange_resolver: Callable[[str], str] | None = None) -> None:
        self.client = client
        self.account_no = account_no.strip()
        self.order_submission_enabled = bool(order_submission_enabled)
        self.default_exchange = default_exchange.strip().upper() or "ND"
        self.exchange_resolver = exchange_resolver

    def get_overseas_balance(self) -> dict[str, Any]:
        pages = self.client.post_all_pages(
            "/api/us/acnt",
            api_id="ust21070",
            body={"stex_tp": "", "stk_cd": ""},
        )
        rows: list[dict[str, Any]] = []
        summary: Mapping[str, Any] = {}
        for page in pages:
            data = page.data
            if not summary:
                summary = data
            result_list = data.get("result_list") or []
            if isinstance(result_list, Mapping):
                result_list = [result_list]
            for item in result_list:
                if isinstance(item, Mapping):
                    rows.append(self._holding(item))
        cash_page = self.client.post("/api/us/acnt", api_id="ust21110", body={})
        cash_rows = cash_page.data.get("result_list") or []
        if isinstance(cash_rows, Mapping):
            cash_rows = [cash_rows]
        usd_cash = next(
            (item for item in cash_rows if isinstance(item, Mapping) and _text(item.get("crnc_code")) == "USD"),
            {},
        )
        return {
            "output1": rows,
            "output2": {
                "crcy_cd": _text(summary.get("crnc_code")) or "USD",
                # ust21070 tot_evlt_amt is the evaluation amount of all US
                # stock holdings, not cash-inclusive account equity.
                "broker_stock_eval": _text(summary.get("tot_evlt_amt")),
                "broker_stock_purchase": _text(summary.get("tot_prch_amt")),
                "broker_stock_pnl": _text(summary.get("tot_pl_amt")),
                "broker_stock_return_rate": _text(summary.get("tot_pl_rt")),
                "frcr_evlu_tota": _text(summary.get("tot_evlt_amt")),
                "tot_evlu_amt": _text(summary.get("tot_evlt_amt")),
                "tot_pfls_amt": _text(summary.get("tot_pl_amt")),
                "frcr_dncl_amt": _text(usd_cash.get("fc_entra")),
                "frcr_drwg_psbl_amt": _text(usd_cash.get("fc_ord_alowa")),
            },
            "output3": {},
            "_broker": "kiwoom",
            "_broker_holding_count": len(rows),
            "_account_configured": bool(self.account_no),
        }

    def place_overseas_order(self, symbol: str, action: str, price: float, qty: float) -> dict[str, Any]:
        if not self.order_submission_enabled:
            raise RuntimeError("Kiwoom US order submission is disabled")
        side = str(action).strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("action must be buy or sell")
        page = self.client.post(
            "/api/us/ordr",
            api_id="ust20000" if side == "buy" else "ust20001",
            body={"stex_tp": self._exchange(symbol), "stk_cd": _text(symbol).upper(), "ord_qty": self._quantity(qty), "ord_uv": self._price(price), "trde_tp": "00"},
            request_kind="order",
        )
        return self._order_response(page.data)

    def revise_overseas_order(self, symbol: str, order_no: str, *, qty: float, price: float) -> dict[str, Any]:
        return self._change_order("ust20002", symbol, order_no, qty=qty, price=price)

    def cancel_overseas_order(self, symbol: str, order_no: str, *, qty: float = 0) -> dict[str, Any]:
        return self._change_order("ust20003", symbol, order_no, qty=qty, price=0)

    def _change_order(self, api_id: str, symbol: str, order_no: str, *, qty: float, price: float) -> dict[str, Any]:
        if not self.order_submission_enabled:
            raise RuntimeError("Kiwoom US order submission is disabled")
        body = {"stex_tp": self._exchange(symbol), "stk_cd": _text(symbol).upper(), "ord_no": _text(order_no), "ord_qty": self._quantity(qty)}
        if api_id == "ust20002":
            body.update({"ord_uv": self._price(price), "trde_tp": "00"})
        page = self.client.post("/api/us/ordr", api_id=api_id, body=body, request_kind="order")
        return self._order_response(page.data)

    @staticmethod
    def _quantity(value: float) -> str:
        quantity = int(float(value))
        if quantity <= 0 or quantity != float(value):
            raise ValueError("quantity must be a positive whole number")
        return str(quantity)

    def _exchange(self, symbol: str) -> str:
        exchange = self.exchange_resolver(symbol) if self.exchange_resolver else self.default_exchange
        exchange = _text(exchange).upper()
        if exchange not in {"ND", "NY", "NA"}:
            raise RuntimeError(f"Kiwoom exchange could not be resolved for {_text(symbol).upper()}")
        return exchange

    @staticmethod
    def _price(value: float) -> str:
        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("price must be a number") from exc
        if not price.is_finite() or price <= 0:
            raise ValueError("price must be positive")
        # Kiwoom US orders accept two decimal places at $1 or above and four
        # below $1.  Use decimal rounding so binary floats cannot leak extra
        # digits into ``ord_uv``.
        tick = Decimal("0.01") if price >= 1 else Decimal("0.0001")
        rounded = price.quantize(tick, rounding=ROUND_HALF_UP)
        return format(rounded, "f")

    @staticmethod
    def _order_response(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"rt_cd": "0", "msg1": _text(payload.get("return_msg")) or "Kiwoom order accepted", "output": {"ODNO": _text(payload.get("ord_no"))}, "_broker": "kiwoom", "raw": dict(payload)}

    @staticmethod
    def circuit_status() -> dict[str, Any]:
        return {"opened": False, "error_count": 0, "max_errors": 0, "opened_at": None}

    @staticmethod
    def reset_circuit() -> None:
        return None

    @staticmethod
    def _holding(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "pdno": _text(item.get("stk_cd")),
            "prdt_name": _text(item.get("frgn_stk_nm")) or _text(item.get("stk_cd")),
            "cblc_qty13": _text(item.get("poss_qty") or item.get("qty")),
            "avg_unpr3": _text(item.get("frgn_stk_book_uv")),
            "ovrs_now_pric1": _text(item.get("now_pric")),
            "frcr_evlu_amt2": _text(item.get("evlt_amt")),
            "evlu_pfls_amt2": _text(item.get("pl_amt")),
            "evlu_pfls_rt1": _text(item.get("pl_rt")),
            "ovrs_excg_cd": _text(item.get("stex_nm")),
        }
