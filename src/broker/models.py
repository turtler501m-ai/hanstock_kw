"""Normalized domestic-stock values used above broker adapters."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    name: str = ""
    quantity: int = 0
    sellable_quantity: int = 0
    average_price: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    profit_loss: float = 0.0
    profit_loss_rate: float = 0.0
    daily_change_rate: float = 0.0
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class AccountBalance:
    holdings: tuple[Holding, ...] = ()
    cash: float = 0.0
    total_equity: float = 0.0
    stock_value: float = 0.0
    profit_loss: float = 0.0
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    current_price: float = 0.0
    ask_price: float = 0.0
    bid_price: float = 0.0
    market_cap: float = 0.0
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class DailyBar:
    date: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float = 0.0
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    quantity: int
    price: int = 0
    exchange: str = "KRX"


@dataclass(frozen=True, slots=True)
class OrderResult:
    success: bool
    message: str = ""
    broker_order_id: str = ""
    status: OrderStatus = OrderStatus.UNKNOWN
    dry_run: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class TradeExecution:
    order_id: str
    symbol: str
    side: OrderSide
    requested_quantity: int = 0
    filled_quantity: int = 0
    remaining_quantity: int = 0
    average_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.UNKNOWN
    ordered_at: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)
