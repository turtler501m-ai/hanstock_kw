"""Domestic-stock broker selection and construction."""

import os
from typing import Any, Callable

from src.broker.base import DomesticStockBroker
from src.broker.kis_adapter import KISBrokerAdapter


SUPPORTED_DOMESTIC_STOCK_BROKERS = frozenset({"kis", "kiwoom"})


def selected_domestic_stock_broker(value: str | None = None) -> str:
    selected = (value or os.environ.get("DOMESTIC_STOCK_BROKER", "kis")).strip().lower()
    if selected not in SUPPORTED_DOMESTIC_STOCK_BROKERS:
        allowed = ", ".join(sorted(SUPPORTED_DOMESTIC_STOCK_BROKERS))
        raise ValueError(f"Unsupported domestic stock broker: {selected!r}. Expected one of: {allowed}")
    return selected


def create_domestic_stock_broker(
    broker: str | None = None,
    *,
    client: Any | None = None,
    kis_client_factory: Callable[..., Any] | None = None,
    notify_errors: bool = False,
) -> DomesticStockBroker:
    selected = selected_domestic_stock_broker(broker)
    if selected == "kiwoom":
        raise NotImplementedError("Kiwoom REST adapter is not implemented yet")
    if client is None:
        if kis_client_factory is None:
            from src.trader import KIStockAPI
            kis_client_factory = KIStockAPI
        client = kis_client_factory(notify_errors=notify_errors)
    return KISBrokerAdapter(client)
