"""Domestic-stock broker selection and construction."""

import os
from typing import Any

from src.broker.base import DomesticStockBroker
SUPPORTED_DOMESTIC_STOCK_BROKERS = frozenset({"kiwoom"})


def selected_domestic_stock_broker(value: str | None = None) -> str:
    selected = (value or os.environ.get("DOMESTIC_STOCK_BROKER", "kiwoom")).strip().lower()
    if selected not in SUPPORTED_DOMESTIC_STOCK_BROKERS:
        allowed = ", ".join(sorted(SUPPORTED_DOMESTIC_STOCK_BROKERS))
        raise ValueError(f"Unsupported domestic stock broker: {selected!r}. Expected one of: {allowed}")
    return selected


def create_domestic_stock_broker(
    broker: str | None = None,
    *,
    client: Any | None = None,
    notify_errors: bool = False,
    settings: Any | None = None,
    order_submission_enabled: bool = False,
) -> DomesticStockBroker:
    selected = selected_domestic_stock_broker(broker)
    from src.broker.kiwoom_adapter import KiwoomBrokerAdapter
    from src.broker.kiwoom_client import KiwoomRestClient

    if client is None:
        if settings is None:
            from src.config import config
            settings = config
        trading_env = str(getattr(settings, "kiwoom_trading_env", "demo") or "demo").lower()
        application_env = str(getattr(settings, "trading_env", trading_env) or trading_env).lower()
        if application_env != trading_env:
            raise ValueError("KIWOOM_TRADING_ENV must match TRADING_ENV before broker activation")
        prefix = "kiwoom_domestic_real" if trading_env == "real" else "kiwoom_domestic_demo"
        app_key = str(getattr(settings, f"{prefix}_app_key", "") or "").strip()
        app_secret = str(getattr(settings, f"{prefix}_app_secret", "") or "").strip()
        if not app_key or not app_secret:
            raise ValueError(f"Kiwoom {trading_env} App Key and App Secret are required")
        client = KiwoomRestClient(app_key, app_secret, environment="live" if trading_env == "real" else "mock")
    return KiwoomBrokerAdapter(client, order_submission_enabled=order_submission_enabled)
