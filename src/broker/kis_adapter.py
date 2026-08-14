"""KIS compatibility adapter and normalized result conversion."""

from typing import Any, Mapping

from src.broker.models import AccountBalance, CancelOrderRequest, DailyBar, Holding, OrderRequest, OrderResult, OrderSide, OrderSnapshot, OrderStatus, Quote, ReviseOrderRequest, TradeExecution


def _number(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    return int(_number(value))


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return ""


class KISBrokerAdapter:
    """Provide typed methods while preserving legacy methods during migration."""

    broker_name = "kis"

    def __init__(self, client: Any) -> None:
        self.client = client

    def fetch_balance(self) -> AccountBalance:
        raw = self.client.get_balance()
        holdings = tuple(self._holding(row) for row in (raw.get("output1") or []))
        summary_rows = raw.get("output2") or []
        summary = summary_rows[0] if summary_rows else {}
        return AccountBalance(
            holdings=holdings,
            cash=_number(_first(summary, "prvs_rcdl_excc_amt", "dnca_tot_amt")),
            total_equity=_number(_first(summary, "tot_evlu_amt", "nass_amt")),
            stock_value=_number(_first(summary, "scts_evlu_amt", "tot_pchs_amt")),
            profit_loss=_number(_first(summary, "evlu_pfls_smtl_amt", "evlu_pfls_amt")),
            raw=raw,
        )

    @staticmethod
    def _holding(row: Mapping[str, Any]) -> Holding:
        quantity = _integer(_first(row, "hldg_qty", "hold_qty"))
        return Holding(
            symbol=str(_first(row, "pdno", "symbol")),
            name=str(_first(row, "prdt_name", "name")),
            quantity=quantity,
            sellable_quantity=_integer(_first(row, "ord_psbl_qty", "sellable_qty")) or quantity,
            average_price=_number(_first(row, "pchs_avg_pric", "avg_price")),
            current_price=_number(_first(row, "prpr", "current_price")),
            market_value=_number(_first(row, "evlu_amt", "evaluation_amount")),
            profit_loss=_number(_first(row, "evlu_pfls_amt", "profit_loss")),
            profit_loss_rate=_number(_first(row, "evlu_pfls_rt", "profit_loss_rate")),
            daily_change_rate=_number(_first(row, "fltt_rt", "daily_change_rate")),
            raw=row,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        raw = self.client.get_quote(symbol)
        return Quote(symbol, _number(_first(raw, "current", "stck_prpr")), _number(_first(raw, "ask1", "askp1")), _number(_first(raw, "bid1", "bidp1")), _number(_first(raw, "market_cap", "hts_avls")), raw)

    def fetch_daily_bars(self, symbol: str, count: int = 60) -> list[DailyBar]:
        return [DailyBar(str(_first(row, "stck_bsop_date", "date")), _number(_first(row, "stck_oprc", "open")), _number(_first(row, "stck_hgpr", "high")), _number(_first(row, "stck_lwpr", "low")), _number(_first(row, "stck_clpr", "close")), _number(_first(row, "acml_vol", "volume")), row) for row in self.client.get_daily(symbol, n=count)]

    def submit_order(self, request: OrderRequest) -> OrderResult:
        raw = self.client.place_order(request.symbol, request.side.value, request.price, request.quantity)
        success = str(raw.get("rt_cd", "")) == "0"
        output = raw.get("output") or {}
        return OrderResult(success, str(raw.get("msg1") or ""), str(_first(output, "ODNO", "odno", "order_no")), OrderStatus.SUBMITTED if success else OrderStatus.REJECTED, str(raw.get("msg1")) == "DRY_RUN", raw)

    def submit_revision(self, request: ReviseOrderRequest) -> OrderResult:
        raw = self.revise_order(
            request.order_id,
            symbol=request.symbol,
            qty=request.quantity,
            price=request.price,
            exchange_id=request.exchange,
        )
        return self._order_result(raw)

    def submit_cancellation(self, request: CancelOrderRequest) -> OrderResult:
        raw = self.cancel_order(
            request.order_id,
            qty=request.quantity,
            exchange_id=request.exchange,
            cancel_all=request.quantity <= 0,
        )
        return self._order_result(raw)

    @staticmethod
    def _order_result(raw: Mapping[str, Any]) -> OrderResult:
        success = str(raw.get("rt_cd", "")) == "0"
        output = raw.get("output") or {}
        return OrderResult(
            success,
            str(raw.get("msg1") or ""),
            str(_first(output, "ODNO", "odno", "order_no")),
            OrderStatus.SUBMITTED if success else OrderStatus.REJECTED,
            str(raw.get("msg1")) == "DRY_RUN",
            raw,
        )

    def fetch_trade_history(self, start_date: str, end_date: str) -> list[TradeExecution]:
        return [self._execution(row) for row in self.client.get_trade_history(start_date, end_date)]

    def fetch_order_snapshot(self, order_id: str, order_date: str = "") -> OrderSnapshot:
        raw = self.client.get_order_snapshot(order_id, order_date=order_date)
        status_value = str(raw.get("status") or "unknown")
        status_aliases = {"partially_filled": OrderStatus.PARTIAL, "cancelled": OrderStatus.CANCELED}
        try:
            status = OrderStatus(status_value)
        except ValueError:
            status = status_aliases.get(status_value, OrderStatus.UNKNOWN)
        return OrderSnapshot(
            broker_order_id=str(raw.get("broker_order_id") or order_id),
            status=status,
            requested_quantity=_integer(raw.get("requested_qty")),
            filled_quantity=_integer(raw.get("cumulative_filled_qty")),
            remaining_quantity=_integer(raw.get("remaining_qty")),
            average_fill_price=_number(raw.get("average_fill_price")),
            message=str(raw.get("message") or ""),
            outcome_unknown=bool(raw.get("outcome_unknown")),
            raw=raw,
        )

    @staticmethod
    def _execution(row: Mapping[str, Any]) -> TradeExecution:
        side_code = str(_first(row, "sll_buy_dvsn_cd", "sll_buy_dvsn_name")).lower()
        side = OrderSide.SELL if side_code in {"01", "sell", "매도"} else OrderSide.BUY
        requested = _integer(_first(row, "ord_qty", "requested_qty"))
        filled = _integer(_first(row, "tot_ccld_qty", "ccld_qty", "filled_qty"))
        remaining = _integer(_first(row, "rmn_qty", "RMN_QTY")) or max(0, requested - filled)
        status = OrderStatus.FILLED if requested and filled >= requested else OrderStatus.PARTIAL if filled else OrderStatus.OPEN
        return TradeExecution(str(_first(row, "odno", "ODNO", "ord_no", "order_no")), str(_first(row, "pdno", "PDNO", "symbol")), side, requested, filled, remaining, _number(_first(row, "avg_prvs", "avg_ccld_pric", "ccld_unpr")), status, str(_first(row, "ord_dt", "ccld_dt", "ord_tmd", "ccld_tmd")), row)

    # Temporary legacy facade; removed after all consumers use normalized models.
    def get_balance(self) -> dict:
        return self.client.get_balance()

    def get_quote(self, symbol: str) -> dict:
        return self.client.get_quote(symbol)

    def get_daily(self, symbol: str, n: int = 60) -> list:
        return self.client.get_daily(symbol, n=n)

    def get_trade_history(self, start_date: str, end_date: str) -> list:
        return self.client.get_trade_history(start_date, end_date)

    def place_order(self, symbol: str, order_type: str, price: int, qty: int) -> dict:
        return self.client.place_order(symbol, order_type, price, qty)

    def cancel_order(self, order_no: str, **kwargs: Any) -> dict:
        kwargs.pop("symbol", None)
        return self.client.cancel_order(order_no, **kwargs)

    def revise_order(self, order_no: str, **kwargs: Any) -> dict:
        kwargs.pop("symbol", None)
        revise = getattr(self.client, "revise_order", None)
        if callable(revise):
            return revise(order_no, **kwargs)
        low_level = getattr(self.client, "_client", None)
        if low_level is not None and hasattr(low_level, "revise_domestic_order"):
            return low_level.revise_domestic_order(order_no, **kwargs)
        raise NotImplementedError("KIS order revision is unavailable for this client")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)
