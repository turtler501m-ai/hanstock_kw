"""Normalize Kiwoom REST domestic-stock responses into broker models."""

from datetime import date, datetime, timedelta
from typing import Any, Mapping

from src.broker.models import (
    AccountBalance,
    CancelOrderRequest,
    DailyBar,
    Holding,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderSnapshot,
    OrderStatus,
    Quote,
    ReviseOrderRequest,
    TradeExecution,
)


def _number(value: Any) -> float:
    """Parse Kiwoom's comma separated, sign-prefixed numeric strings."""
    if value is None or isinstance(value, bool):
        return 0.0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    return int(_number(value))


def _price(value: Any) -> float:
    """Kiwoom prefixes prices with the price-direction sign; prices are absolute."""
    return abs(_number(value))


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return ""


def _rows(payload: Any, *keys: str) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, Mapping)]
    return []


def _strip_market_prefix(symbol: Any) -> str:
    text = str(symbol or "").strip()
    # Kiwoom may return A005930/J005930 rather than the six-digit stock code.
    if len(text) == 7 and text[0].isalpha() and text[1:].isdigit():
        return text[1:]
    return text


def _data(page: Any) -> Mapping[str, Any]:
    value = getattr(page, "data", page)
    return value if isinstance(value, Mapping) else {}


class KiwoomBrokerAdapter:
    """Typed read adapter over a dependency-injected Kiwoom REST client."""

    broker_name = "kiwoom"

    def __init__(self, client: Any, *, order_submission_enabled: bool = False) -> None:
        self.client = client
        self.order_submission_enabled = bool(order_submission_enabled)

    def fetch_balance(self) -> AccountBalance:
        balance_pages = self.client.post_all_pages(
            "/api/dostk/acnt", api_id="kt00018", body={"qry_tp": "1", "dmst_stex_tp": "KRX"}
        )
        deposit_page = self.client.post(
            "/api/dostk/acnt", api_id="kt00001", body={"qry_tp": "3"}
        )
        balance = _data(balance_pages[0]) if balance_pages else {}
        deposit = _data(deposit_page)
        holding_rows = []
        for page in balance_pages:
            holding_rows.extend(_rows(
                _data(page),
                "acnt_evlt_remn_indv_tot",
                "acnt_evlt_remn_indv",
                "output",
                "output1",
                "holdings",
            ))
        holdings = tuple(self._holding(row) for row in holding_rows)
        orderable_cash = _number(_first(deposit, "ord_alow_amt", "entr", "cash", "dnca_tot_amt"))
        summary_stock_value = _number(_first(balance, "tot_evlt_amt", "tot_evlu_amt", "stock_value"))
        holding_stock_value = sum(row.market_value for row in holdings)
        stock_value = holding_stock_value if holdings else summary_stock_value
        # kt00018 names the estimated deposit assets field differently from
        # the legacy dictionary-shaped response consumed by the dashboard.
        total_equity = _number(_first(
            balance,
            "prsm_dpst_aset_amt",
            "tot_asst_amt",
            "estimated_deposit",
            "total_equity",
        ))
        if not total_equity:
            total_equity = orderable_cash + stock_value
        # ord_alow_amt is buying power, not the cash component of account
        # equity. Use the broker's own account equation so the dashboard's
        # cash + stock evaluation always reconciles to estimated assets.
        cash = (
            total_equity - stock_value
            if total_equity > 0 and stock_value >= 0 and total_equity >= stock_value
            else orderable_cash
        )
        return AccountBalance(
            holdings=holdings,
            cash=cash,
            orderable_cash=orderable_cash,
            total_equity=total_equity,
            stock_value=stock_value,
            profit_loss=_number(_first(balance, "tot_evlt_pl", "tot_pl_amt", "profit_loss")),
            raw={"kt00018": balance, "kt00001": deposit},
        )

    @staticmethod
    def _holding(row: Mapping[str, Any]) -> Holding:
        quantity = _integer(_first(row, "rmnd_qty", "hold_qty", "hldg_qty", "quantity"))
        current_price = _price(_first(row, "cur_prc", "cur_price", "prpr", "current_price"))
        previous_close = _price(_first(row, "pred_close_pric", "stck_sdpr", "previous_close"))
        daily_change_rate = _number(_first(row, "flu_rt", "daily_change_rate"))
        if not daily_change_rate and current_price > 0 and previous_close > 0:
            daily_change_rate = (current_price / previous_close - 1.0) * 100.0
        return Holding(
            symbol=_strip_market_prefix(_first(row, "stk_cd", "stk_code", "pdno", "symbol")),
            name=str(_first(row, "stk_nm", "stk_name", "prdt_name", "name")),
            quantity=quantity,
            sellable_quantity=_integer(_first(row, "trde_able_qty", "ord_psbl_qty", "sellable_quantity")),
            average_price=_price(_first(row, "pur_pric", "avg_pric", "pchs_avg_pric", "average_price")),
            current_price=current_price,
            market_value=_number(_first(row, "evlt_amt", "evltv_prft", "market_value")),
            profit_loss=_number(_first(row, "evlt_pl", "evltv_prft", "profit_loss")),
            profit_loss_rate=_number(_first(row, "prft_rt", "evlu_pfls_rt", "profit_loss_rate")),
            daily_change_rate=daily_change_rate,
            raw=row,
        )

    def fetch_quote(self, symbol: str) -> Quote:
        row = _data(self.client.post(
            "/api/dostk/stkinfo", api_id="ka10001", body={"stk_cd": symbol}
        ))
        return Quote(
            symbol=_strip_market_prefix(_first(row, "stk_cd", "code", "symbol")) or symbol,
            current_price=_price(_first(row, "cur_prc", "current_price", "stck_prpr")),
            ask_price=_price(_first(row, "sel_fpr_bid", "best_ask_prc", "ask1", "ask_price")),
            bid_price=_price(_first(row, "buy_fpr_bid", "best_bid_prc", "bid1", "bid_price")),
            market_cap=_number(_first(row, "mac", "market_cap", "hts_avls")),
            raw=row,
        )

    def fetch_daily_bars(self, symbol: str, count: int = 60) -> list[DailyBar]:
        pages = self.client.post_all_pages(
            "/api/dostk/chart",
            api_id="ka10081",
            body={"stk_cd": symbol, "base_dt": date.today().strftime("%Y%m%d"), "upd_stkpc_tp": "1"},
            # Chart pages contain up to 100 observations. Do not download the
            # instrument's complete history when a bounded window was asked for.
            max_pages=max(1, (max(0, count) + 99) // 100),
            allow_partial=True,
        )
        rows = [
            row
            for page in pages
            for row in _rows(_data(page), "stk_dt_pole_chart_qry", "output", "output1", "bars")
        ]
        return [
            DailyBar(
                date=str(_first(row, "dt", "date", "stck_bsop_date")),
                open_price=_price(_first(row, "open_pric", "open", "stck_oprc")),
                high_price=_price(_first(row, "high_pric", "high", "stck_hgpr")),
                low_price=_price(_first(row, "low_pric", "low", "stck_lwpr")),
                close_price=_price(_first(row, "cur_prc", "close", "stck_clpr")),
                volume=_number(_first(row, "trde_qty", "volume", "acml_vol")),
                raw=row,
            )
            for row in rows[: max(0, count)]
        ]

    def fetch_volume_rank(self, top_n: int = 50) -> list[str]:
        pages = self.client.post_all_pages(
            "/api/dostk/rkinfo",
            api_id="ka10030",
            body={
                "mrkt_tp": "000", "sort_tp": "1", "mang_stk_incls": "0", "crd_tp": "0",
                "trde_qty_tp": "0", "pric_tp": "0", "trde_prica_tp": "0", "mrkt_open_tp": "0",
                "stex_tp": "1",
            },
        )
        rows = [
            row
            for page in pages
            for row in _rows(_data(page), "trde_qty_upper", "output", "output1", "items")
        ]
        symbols = [_strip_market_prefix(_first(row, "stk_cd", "code", "symbol")) for row in rows]
        return [symbol for symbol in symbols if symbol][: max(0, top_n)]

    def submit_order(self, request: OrderRequest) -> OrderResult:
        if not self.order_submission_enabled:
            return OrderResult(True, "DRY_RUN", status=OrderStatus.SUBMITTED, dry_run=True)
        api_id = "kt10000" if request.side == OrderSide.BUY else "kt10001"
        page = self.client.post(
            "/api/dostk/ordr",
            api_id=api_id,
            body={
                "dmst_stex_tp": request.exchange,
                "stk_cd": request.symbol,
                "ord_qty": str(request.quantity),
                "ord_uv": str(request.price) if request.price else "",
                "trde_tp": "3" if request.price == 0 else "0",
            },
            request_kind="order",
        )
        return self._order_result(_data(page))

    def submit_revision(self, request: ReviseOrderRequest) -> OrderResult:
        if not self.order_submission_enabled:
            return OrderResult(True, "DRY_RUN", status=OrderStatus.SUBMITTED, dry_run=True)
        page = self.client.post(
            "/api/dostk/ordr",
            api_id="kt10002",
            body={
                "dmst_stex_tp": request.exchange,
                "orig_ord_no": request.order_id,
                "stk_cd": request.symbol,
                "mdfy_qty": str(request.quantity),
                "mdfy_uv": str(request.price),
                "mdfy_cond_uv": "",
            },
            request_kind="order",
        )
        return self._order_result(_data(page))

    def submit_cancellation(self, request: CancelOrderRequest) -> OrderResult:
        if not self.order_submission_enabled:
            return OrderResult(True, "DRY_RUN", status=OrderStatus.CANCELED, dry_run=True)
        page = self.client.post(
            "/api/dostk/ordr",
            api_id="kt10003",
            body={
                "dmst_stex_tp": request.exchange,
                "orig_ord_no": request.order_id,
                "stk_cd": request.symbol,
                "cncl_qty": str(max(0, request.quantity)),
            },
            request_kind="order",
        )
        return self._order_result(_data(page), success_status=OrderStatus.CANCELED)

    @staticmethod
    def _order_result(payload: Mapping[str, Any], *, success_status: OrderStatus = OrderStatus.SUBMITTED) -> OrderResult:
        code = str(_first(payload, "return_code", "rt_cd"))
        success = code in {"", "0"}
        return OrderResult(
            success=success,
            message=str(_first(payload, "return_msg", "msg1")),
            broker_order_id=str(_first(payload, "ord_no", "order_no", "ODNO")),
            status=success_status if success else OrderStatus.REJECTED,
            raw=payload,
        )

    def fetch_trade_history(self, start_date: str, end_date: str) -> list[TradeExecution]:
        start = datetime.strptime(start_date.replace("-", ""), "%Y%m%d").date()
        end = datetime.strptime(end_date.replace("-", ""), "%Y%m%d").date()
        if start > end:
            start, end = end, start
        rows: list[Mapping[str, Any]] = []
        current = start
        while current <= end:
            # kt00007 accepts one order date per request, not a date range.
            if current.weekday() < 5:
                pages = self.client.post_all_pages(
                    "/api/dostk/acnt",
                    api_id="kt00007",
                    body={
                        "ord_dt": current.strftime("%Y%m%d"),
                        "qry_tp": "1",
                        "stk_bond_tp": "1",
                        "sell_tp": "0",
                        "stk_cd": "",
                        "fr_ord_no": "",
                        "dmst_stex_tp": "%",
                    },
                )
                order_date = current.strftime("%Y%m%d")
                for page in pages:
                    for row in _rows(
                        _data(page), "acnt_ord_cntr_prps_dtl", "ord_cntr_dtl", "output", "output1"
                    ):
                        rows.append({**row, "ord_dt": str(row.get("ord_dt") or order_date)})
            current += timedelta(days=1)
        return [self._execution(row) for row in rows]

    @staticmethod
    def _execution(row: Mapping[str, Any]) -> TradeExecution:
        requested = _integer(_first(row, "ord_qty", "requested_qty"))
        # kt00007's cnfm_qty is the acknowledged order quantity, not the
        # executed quantity. Treating it as a fill turns open orders into
        # zero-price executions. cntr_qty is the actual executed quantity.
        filled = _integer(_first(row, "cntr_qty", "tot_cntr_qty", "filled_qty"))
        remaining = _integer(_first(row, "ord_remnq", "oso_qty", "rmn_qty", "remaining_qty"))
        if not remaining:
            remaining = max(0, requested - filled)
        side_text = str(_first(row, "io_tp_nm", "io_tp", "sell_tp", "side")).lower()
        side = OrderSide.SELL if "매도" in side_text or side_text in {"1", "01", "sell"} else OrderSide.BUY
        status = OrderStatus.FILLED if requested and filled >= requested else OrderStatus.PARTIAL if filled else OrderStatus.OPEN
        return TradeExecution(
            order_id=str(_first(row, "ord_no", "order_no", "ODNO")),
            symbol=_strip_market_prefix(_first(row, "stk_cd", "symbol", "pdno")),
            side=side,
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=remaining,
            average_fill_price=_price(_first(row, "cntr_uv", "avg_prc", "cntr_pric", "average_fill_price")),
            status=status,
            ordered_at=str(_first(row, "ord_dt", "ord_tm", "ordered_at")),
            raw=row,
        )

    def fetch_order_snapshot(self, order_id: str, order_date: str = "") -> OrderSnapshot:
        history = self.fetch_trade_history(order_date, order_date)
        match = next((row for row in history if row.order_id == order_id), None)
        if match is None:
            return OrderSnapshot(order_id, outcome_unknown=True, message="Order not found")
        return OrderSnapshot(
            broker_order_id=match.order_id,
            status=match.status,
            requested_quantity=match.requested_quantity,
            filled_quantity=match.filled_quantity,
            remaining_quantity=match.remaining_quantity,
            average_fill_price=match.average_fill_price,
            raw=match.raw,
        )

    # Dictionary compatibility methods used while dashboard call sites migrate.
    def get_balance(self) -> dict[str, Any]:
        balance = self.fetch_balance()
        integer_text = lambda value: str(int(round(float(value or 0))))
        return {
            "rt_cd": "0",
            "msg1": "",
            "output1": [{
                "pdno": row.symbol, "prdt_name": row.name, "hldg_qty": str(row.quantity),
                "ord_psbl_qty": str(row.sellable_quantity), "pchs_avg_pric": integer_text(row.average_price),
                "prpr": integer_text(row.current_price), "evlu_amt": integer_text(row.market_value),
                "evlu_pfls_amt": integer_text(row.profit_loss), "evlu_pfls_rt": str(row.profit_loss_rate),
                "fltt_rt": str(row.daily_change_rate),
            } for row in balance.holdings],
            "output2": [{
                "prvs_rcdl_excc_amt": integer_text(balance.cash), "dnca_tot_amt": integer_text(balance.cash),
                "ord_psbl_cash": integer_text(balance.orderable_cash),
                "tot_evlu_amt": integer_text(balance.total_equity), "scts_evlu_amt": integer_text(balance.stock_value),
                "evlu_pfls_smtl_amt": integer_text(balance.profit_loss),
            }],
            "_broker": "kiwoom",
        }

    def get_quote(self, symbol: str) -> dict[str, float]:
        quote = self.fetch_quote(symbol)
        return {"current": quote.current_price, "ask1": quote.ask_price, "bid1": quote.bid_price, "market_cap": quote.market_cap}

    def get_daily(self, symbol: str, n: int = 60) -> list[dict[str, Any]]:
        return [{"stck_bsop_date": row.date, "stck_oprc": str(row.open_price), "stck_hgpr": str(row.high_price), "stck_lwpr": str(row.low_price), "stck_clpr": str(row.close_price), "acml_vol": str(row.volume)} for row in self.fetch_daily_bars(symbol, n)]

    def get_index_daily(self, index_code: str, n: int = 90) -> list[dict[str, Any]]:
        kiwoom_code = {"0001": "001", "1001": "101"}.get(index_code, index_code)
        pages = self.client.post_all_pages(
            "/api/dostk/chart",
            api_id="ka20006",
            body={
                "inds_cd": kiwoom_code,
                "base_dt": date.today().strftime("%Y%m%d"),
            },
            max_pages=max(1, (max(0, n) + 99) // 100),
            allow_partial=True,
        )
        rows = [
            row
            for page in pages
            for row in _rows(
                _data(page),
                # ka20006 names the response array differently from ka10081.
                "inds_dt_pole_qry",
                "inds_dt_pole_chart_qry",
                "output",
                "output1",
                "bars",
            )
        ]
        index_price = lambda value: _price(value) / 100.0
        return [{
            "date": str(_first(row, "dt", "date")),
            "open": index_price(_first(row, "open_pric", "open")),
            "high": index_price(_first(row, "high_pric", "high")),
            "low": index_price(_first(row, "low_pric", "low")),
            "close": index_price(_first(row, "cur_prc", "close")),
            "volume": _number(_first(row, "trde_qty", "volume")),
        } for row in rows[: max(0, n)]]

    def get_volume_rank(self, top_n: int = 50) -> list[str]:
        return self.fetch_volume_rank(top_n)

    def place_order(self, symbol: str, order_type: str, price: int, qty: int) -> dict[str, Any]:
        result = self.submit_order(OrderRequest(symbol, OrderSide(order_type), qty, price))
        return self._legacy_order_result(result)

    def cancel_order(self, order_no: str, *, qty: int = 0, exchange_id: str = "KRX", symbol: str = "", **_: Any) -> dict[str, Any]:
        return self._legacy_order_result(self.submit_cancellation(CancelOrderRequest(order_no, symbol, qty, exchange_id)))

    def revise_order(self, order_no: str, *, symbol: str = "", qty: int = 0, price: int = 0, exchange_id: str = "KRX", **_: Any) -> dict[str, Any]:
        return self._legacy_order_result(self.submit_revision(ReviseOrderRequest(order_no, symbol, qty, price, exchange_id)))

    def get_trade_history(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return [dict(row.raw) for row in self.fetch_trade_history(start_date, end_date)]

    def get_order_snapshot(self, order_no: str, order_date: str = "", **_: Any) -> dict[str, Any]:
        row = self.fetch_order_snapshot(order_no, order_date)
        return {"status": row.status.value, "broker_order_id": row.broker_order_id, "cumulative_filled_qty": row.filled_quantity, "average_fill_price": row.average_fill_price, "requested_qty": row.requested_quantity, "remaining_qty": row.remaining_quantity, "message": row.message, "outcome_unknown": row.outcome_unknown, "payload": dict(row.raw)}

    @staticmethod
    def _legacy_order_result(result: OrderResult) -> dict[str, Any]:
        return {"rt_cd": "0" if result.success else "1", "msg1": result.message, "output": {"ODNO": result.broker_order_id}, "_broker": "kiwoom", "_dry_run": result.dry_run, "raw": dict(result.raw)}
