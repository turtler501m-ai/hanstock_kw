"""Training label helpers for strategy model datasets."""
from __future__ import annotations


def future_return(prices: list[float], index: int, horizon: int) -> float | None:
    if index < 0 or horizon <= 0 or index + horizon >= len(prices):
        return None
    entry = prices[index]
    exit_price = prices[index + horizon]
    if entry <= 0:
        return None
    return (exit_price / entry) - 1


def max_forward_drawdown(prices: list[float], index: int, horizon: int) -> float | None:
    if index < 0 or horizon <= 0 or index + 1 >= len(prices):
        return None
    end = min(len(prices), index + horizon + 1)
    entry = prices[index]
    if entry <= 0:
        return None
    lowest = min(prices[index + 1:end])
    return abs((lowest / entry) - 1)


def buy_quality_label(
    prices: list[float],
    index: int,
    *,
    horizon: int = 20,
    min_return: float = 0.03,
    drawdown_penalty: float = 0.5,
) -> int | None:
    """Label entries that cleared a return target after drawdown penalty."""
    realized = future_return(prices, index, horizon)
    drawdown = max_forward_drawdown(prices, index, horizon)
    if realized is None or drawdown is None:
        return None
    return 1 if realized - (drawdown * drawdown_penalty) >= min_return else 0
