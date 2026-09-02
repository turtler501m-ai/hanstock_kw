from __future__ import annotations

from collections.abc import Mapping, Sequence


def simulate_target_portfolio(
    target_weights_by_day: Sequence[Mapping[str, float]],
    returns_by_day: Sequence[Mapping[str, float]],
    *,
    initial_capital: float,
    commission_bps: float,
    slippage_bps: float,
    market_impact_bps: float,
    sell_tax_bps: float = 0.0,
    rebalance_threshold: float = 0.02,
) -> dict:
    """Simulate target-weight rebalancing with explicit trading costs.

    Each item represents one close-to-close holding period. Small target changes
    remain untraded so the result reflects an executable rebalance policy.
    """
    if len(target_weights_by_day) != len(returns_by_day):
        raise ValueError("target weights and returns must have the same length")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    threshold = max(0.0, float(rebalance_threshold))
    buy_cost_bps = max(0.0, commission_bps + slippage_bps + market_impact_bps)
    sell_cost_bps = max(0.0, buy_cost_bps + sell_tax_bps)
    current_weights: dict[str, float] = {}
    portfolio_value = float(initial_capital)
    equity_curve = [portfolio_value]
    peak = portfolio_value
    max_drawdown = 0.0
    trade_count = 0
    rebalance_count = 0
    buy_turnover = 0.0
    sell_turnover = 0.0
    total_cost_amount = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    winning_periods = 0
    losing_periods = 0

    for raw_targets, period_returns in zip(target_weights_by_day, returns_by_day):
        targets = {
            str(symbol): max(0.0, float(weight))
            for symbol, weight in raw_targets.items()
            if float(weight) > 0.0
        }
        target_total = sum(targets.values())
        if target_total > 1.0:
            targets = {symbol: weight / target_total for symbol, weight in targets.items()}

        executed_weights = dict(current_weights)
        period_buy_turnover = 0.0
        period_sell_turnover = 0.0
        period_trades = 0
        for symbol in set(current_weights) | set(targets):
            current = float(current_weights.get(symbol, 0.0))
            target = float(targets.get(symbol, 0.0))
            delta = target - current
            if abs(delta) < threshold:
                continue
            executed_weights[symbol] = target
            period_trades += 1
            if delta > 0:
                period_buy_turnover += delta
            else:
                period_sell_turnover += abs(delta)

        executed_weights = {
            symbol: weight for symbol, weight in executed_weights.items() if weight > 1e-12
        }
        trade_count += period_trades
        if period_trades:
            rebalance_count += 1
        buy_turnover += period_buy_turnover
        sell_turnover += period_sell_turnover

        cost_fraction = (
            period_buy_turnover * buy_cost_bps
            + period_sell_turnover * sell_cost_bps
        ) / 10_000.0
        cost_amount = portfolio_value * cost_fraction
        total_cost_amount += cost_amount

        gross_return = sum(
            weight * float(period_returns.get(symbol, 0.0))
            for symbol, weight in executed_weights.items()
        )
        previous_value = portfolio_value
        portfolio_value = max(0.0, (portfolio_value - cost_amount) * (1.0 + gross_return))
        net_pnl = portfolio_value - previous_value
        if net_pnl > 0:
            winning_periods += 1
            gross_profit += net_pnl
        elif net_pnl < 0:
            losing_periods += 1
            gross_loss += abs(net_pnl)

        gross_denominator = 1.0 + gross_return
        if gross_denominator > 0:
            current_weights = {
                symbol: weight * (1.0 + float(period_returns.get(symbol, 0.0))) / gross_denominator
                for symbol, weight in executed_weights.items()
                if weight > 1e-12
            }
        else:
            current_weights = {}

        equity_curve.append(portfolio_value)
        peak = max(peak, portfolio_value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - portfolio_value) / peak)

    active_periods = winning_periods + losing_periods
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    return {
        "equity_curve": equity_curve,
        "metrics": {
            "trade_count": trade_count,
            "rebalance_count": rebalance_count,
            "win_rate": round(winning_periods / active_periods, 3) if active_periods else 0.0,
            "profit_factor": round(profit_factor, 2),
            "total_return_pct": round((portfolio_value / initial_capital - 1.0) * 100.0, 2),
            "max_drawdown_pct": round(max_drawdown * 100.0, 2),
            "buy_turnover_pct": round(buy_turnover * 100.0, 2),
            "sell_turnover_pct": round(sell_turnover * 100.0, 2),
            "total_turnover_pct": round((buy_turnover + sell_turnover) * 100.0, 2),
        },
        "costs": {
            "commission_bps": float(commission_bps),
            "slippage_bps": float(slippage_bps),
            "market_impact_bps": float(market_impact_bps),
            "sell_tax_bps": float(sell_tax_bps),
            "rebalance_threshold_pct": round(threshold * 100.0, 3),
            "total_cost_amount": round(total_cost_amount, 2),
            "total_cost_pct_initial": round(total_cost_amount / initial_capital * 100.0, 3),
            "modeled": True,
            "applied_to_returns": True,
        },
    }
