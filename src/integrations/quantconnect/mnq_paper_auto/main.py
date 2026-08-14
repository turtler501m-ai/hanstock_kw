from AlgorithmImports import *
from datetime import timedelta


class MnqPaperAutoStrategy(QCAlgorithm):
    """QuantConnect Paper Trading strategy for CME MNQ futures.

    Deploy this project with the QuantConnect Paper Trading brokerage. It is a
    small moving-average example whose purpose is to verify automatic paper
    order flow on Micro E-mini Nasdaq-100 futures, not to be a production edge.
    """

    def initialize(self) -> None:
        self.set_start_date(2025, 1, 1)
        self.set_cash(100000)

        self._fast_period = int(self.get_parameter("FAST_PERIOD") or 12)
        self._slow_period = int(self.get_parameter("SLOW_PERIOD") or 48)
        self._max_contracts = int(self.get_parameter("MAX_CONTRACTS") or 3)
        self._daily_loss_limit = float(self.get_parameter("DAILY_LOSS_LIMIT") or 750)

        if self._fast_period <= 0 or self._slow_period <= self._fast_period:
            raise ValueError("SLOW_PERIOD must be greater than FAST_PERIOD")
        if self._max_contracts < 1:
            raise ValueError("MAX_CONTRACTS must be at least 1")

        self._future = self.add_future(
            Futures.Indices.MICRO_NASDAQ_100_E_MINI,
            extended_market_hours=True,
            data_normalization_mode=DataNormalizationMode.BACKWARDS_RATIO,
            data_mapping_mode=DataMappingMode.OPEN_INTEREST,
        )
        self._future.set_filter(lambda universe: universe.expiration(7, 90))

        self._fast = self.sma(self._future.symbol, self._fast_period, Resolution.MINUTE)
        self._slow = self.sma(self._future.symbol, self._slow_period, Resolution.MINUTE)
        self.warm_up_indicator(self._future.symbol, self._fast, Resolution.MINUTE)
        self.warm_up_indicator(self._future.symbol, self._slow, Resolution.MINUTE)

        self._active_contract = None
        self._last_trade_bar = None
        self._session_start_value = None
        self._session_day = None

    def on_data(self, slice: Slice) -> None:
        self._reset_daily_risk_state()

        if self.is_warming_up or not self._fast.is_ready or not self._slow.is_ready:
            return
        if self._daily_loss_limit_reached():
            self._flatten("daily loss limit")
            return

        contract = self._select_front_contract(slice)
        if contract is None:
            return

        self._roll_active_contract(contract.symbol)
        self._active_contract = contract.symbol
        holding = self.portfolio[contract.symbol]
        target_quantity = self._target_quantity(holding)
        delta = target_quantity - holding.quantity

        if delta == 0 or self._already_traded_this_bar():
            return

        self.market_order(contract.symbol, delta)
        self._last_trade_bar = slice.time
        self.debug(f"{slice.time}: MNQ paper target={target_quantity}, delta={delta}")

    def on_command(self, data) -> bool:
        command_type = self._command_value(data, "command_type", "CommandType")
        if str(command_type or "").lower() != "order":
            return False

        side = str(self._command_value(data, "side", "Side") or "").lower()
        quantity = int(float(self._command_value(data, "quantity", "Quantity") or 0))
        if side not in {"buy", "sell"} or quantity < 1:
            self.error(f"Invalid dashboard order command: {data}")
            return False
        if quantity > self._max_contracts:
            self.error(f"Dashboard order rejected: quantity {quantity} exceeds max {self._max_contracts}")
            return False

        contract_symbol = self._active_contract
        if contract_symbol is None:
            self.error("Dashboard order rejected: active MNQ contract is not ready")
            return False

        signed_quantity = quantity if side == "buy" else -quantity
        tag = str(self._command_value(data, "tag", "Tag") or "dashboard")
        self.market_order(contract_symbol, signed_quantity, tag=tag)
        self.debug(f"Dashboard MNQ paper order sent: {side} {quantity} {contract_symbol}")
        return True

    def _command_value(self, data, *keys):
        for key in keys:
            if hasattr(data, "get"):
                value = data.get(key)
            else:
                value = getattr(data, key, None)
            if value is not None:
                return value
        return None

    def _select_front_contract(self, slice: Slice):
        chain = slice.future_chains.get(self._future.symbol)
        if not chain:
            return None

        min_expiry = self.time + timedelta(days=7)
        candidates = [contract for contract in chain if contract.expiry > min_expiry]
        if not candidates:
            return None

        return min(candidates, key=lambda contract: contract.expiry)

    def _target_quantity(self, holding) -> int:
        fast = self._fast.current.value
        slow = self._slow.current.value

        if fast > slow and holding.quantity <= 0:
            return self._max_contracts
        if fast < slow and holding.quantity >= 0:
            return -self._max_contracts
        return int(holding.quantity)

    def _roll_active_contract(self, next_symbol) -> None:
        if self._active_contract is None or self._active_contract == next_symbol:
            return

        old_holding = self.portfolio[self._active_contract]
        if old_holding.invested:
            self.liquidate(self._active_contract, "roll to front MNQ contract")

    def _already_traded_this_bar(self) -> bool:
        return self._last_trade_bar == self.time

    def _reset_daily_risk_state(self) -> None:
        if self._session_day == self.time.date():
            return

        self._session_day = self.time.date()
        self._session_start_value = self.portfolio.total_portfolio_value

    def _daily_loss_limit_reached(self) -> bool:
        if self._session_start_value is None:
            return False

        pnl = self.portfolio.total_portfolio_value - self._session_start_value
        return pnl <= -abs(self._daily_loss_limit)

    def _flatten(self, reason: str) -> None:
        if self._active_contract is None:
            return

        holding = self.portfolio[self._active_contract]
        if holding.invested:
            self.liquidate(self._active_contract, reason)
