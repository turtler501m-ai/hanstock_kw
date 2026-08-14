"""Protocol implemented by domestic-stock broker adapters."""

from typing import Protocol, runtime_checkable

from src.broker.models import (
    AccountBalance,
    CancelOrderRequest,
    DailyBar,
    OrderRequest,
    OrderResult,
    OrderSnapshot,
    Quote,
    ReviseOrderRequest,
    TradeExecution,
)


@runtime_checkable
class DomesticStockBroker(Protocol):
    @property
    def broker_name(self) -> str: ...

    def fetch_balance(self) -> AccountBalance: ...

    def fetch_quote(self, symbol: str) -> Quote: ...

    def fetch_daily_bars(self, symbol: str, count: int = 60) -> list[DailyBar]: ...

    def submit_order(self, request: OrderRequest) -> OrderResult: ...

    def submit_revision(self, request: ReviseOrderRequest) -> OrderResult: ...

    def submit_cancellation(self, request: CancelOrderRequest) -> OrderResult: ...

    def fetch_trade_history(self, start_date: str, end_date: str) -> list[TradeExecution]: ...

    def fetch_order_snapshot(self, order_id: str, order_date: str = "") -> OrderSnapshot: ...
