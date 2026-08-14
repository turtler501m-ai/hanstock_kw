from __future__ import annotations

import os
from typing import Any


SYMBOL_MAP = {
    "NQ": "BTCUSDT",
    "MNQ": "BTCUSDT",
    "ES": "USDTUSDC",
    "MES": "USDTUSDC",
    "GC": "GOLDUSDT",
    "MGC": "GOLDUSDT",
    "CL": "WTICOUSDT",
    "MCL": "WTICOUSDT",
}


class BybitTrader:
    def __init__(self, symbol: str = "BTCUSDT", qty: float = 0.001):
        from src.online_access import require_online_access

        require_online_access("Bybit API access")
        from pybit.unified_trading import HTTP

        self.symbol = symbol
        self.qty = str(qty)
        self.session = HTTP(
            testnet=os.getenv("BYBIT_TESTNET", "true").lower() == "true",
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
        )

    def get_price(self) -> float:
        ticker = self.session.get_tickers(category="linear", symbol=self.symbol)
        return float(ticker["result"]["list"][0]["lastPrice"])

    def buy(self) -> dict[str, Any]:
        return self.session.place_order(
            category="linear",
            symbol=self.symbol,
            side="Buy",
            orderType="Market",
            qty=self.qty,
        )

    def sell(self) -> dict[str, Any]:
        return self.session.place_order(
            category="linear",
            symbol=self.symbol,
            side="Sell",
            orderType="Market",
            qty=self.qty,
        )

    def close_position(self) -> dict[str, Any] | None:
        position = self.get_position()
        if position and float(position["size"]) > 0:
            side = "Sell" if position["side"] == "Buy" else "Buy"
            return self.session.place_order(
                category="linear",
                symbol=self.symbol,
                side=side,
                orderType="Market",
                qty=position["size"],
            )
        return None

    def get_position(self) -> dict[str, Any] | None:
        result = self.session.get_positions(category="linear", symbol=self.symbol)
        positions = result["result"]["list"]
        if positions and float(positions[0]["size"]) > 0:
            return positions[0]
        return None

    def get_pnl(self) -> dict[str, Any] | None:
        position = self.get_position()
        if not position:
            return None
        return {
            "side": position["side"],
            "size": position["size"],
            "entry_price": float(position["avgPrice"]),
            "current_price": self.get_price(),
            "unrealized_pnl": float(position["unrealizedPnl"]),
        }

    def process_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        direction = str(signal.get("direction", "")).lower()

        if direction == "exit":
            result = self.close_position()
            if result:
                return {"status": "closed", "order_id": result["result"]["orderId"]}
            return {"status": "no_position"}

        symbol = str(signal.get("symbol", "NQ")).upper()
        bybit_symbol = SYMBOL_MAP.get(symbol, "BTCUSDT")
        if bybit_symbol != self.symbol:
            self.symbol = bybit_symbol

        if direction in {"long", "buy"}:
            result = self.buy()
            return {"status": "buy", "order_id": result["result"]["orderId"]}

        if direction in {"short", "sell"}:
            result = self.sell()
            return {"status": "sell", "order_id": result["result"]["orderId"]}

        return {"status": "unknown_direction"}
