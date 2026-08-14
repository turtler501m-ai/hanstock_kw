from __future__ import annotations

from collections.abc import Iterable

from .models import FuturesSignal, OhlcCandle, VerificationResult


def verify_signal(signal: FuturesSignal, candles: Iterable[OhlcCandle]) -> VerificationResult:
    ordered_targets = _ordered_targets(signal)
    if not ordered_targets:
        return VerificationResult(outcome="invalid", status="rejected", reason="signal has no take profit")

    for candle in candles:
        if not _valid_candle(candle):
            return VerificationResult(outcome="invalid", status="rejected", hit_at=candle.timestamp, reason="invalid OHLC candle")

        stop_hit = _stop_hit(signal, candle)
        target = _first_target_hit(signal, candle, ordered_targets)

        if stop_hit and target is not None:
            index, price = target
            return VerificationResult(
                outcome="ambiguous",
                status="needs_review",
                hit_at=candle.timestamp,
                hit_price=price,
                hit_target_index=index,
                requires_manual_review=True,
                reason="take profit and stop loss were both inside the same candle",
            )
        if target is not None:
            index, price = target
            return VerificationResult(
                outcome="tp",
                status="verified",
                hit_at=candle.timestamp,
                hit_price=price,
                hit_target_index=index,
                reason=f"take profit {index} hit before stop loss",
            )
        if stop_hit:
            return VerificationResult(
                outcome="sl",
                status="rejected",
                hit_at=candle.timestamp,
                hit_price=signal.stop_loss,
                reason="stop loss hit before take profit",
            )

    return VerificationResult(outcome="no_hit", status="pending", reason="no take profit or stop loss hit")


def _ordered_targets(signal: FuturesSignal) -> list[tuple[int, float]]:
    targets = list(enumerate(signal.take_profits, start=1))
    if signal.direction == "long":
        return sorted(targets, key=lambda item: item[1])
    return sorted(targets, key=lambda item: item[1], reverse=True)


def _first_target_hit(
    signal: FuturesSignal,
    candle: OhlcCandle,
    ordered_targets: list[tuple[int, float]],
) -> tuple[int, float] | None:
    for index, price in ordered_targets:
        if signal.direction == "long" and candle.high >= price:
            return index, price
        if signal.direction == "short" and candle.low <= price:
            return index, price
    return None


def _stop_hit(signal: FuturesSignal, candle: OhlcCandle) -> bool:
    if signal.direction == "long":
        return candle.low <= signal.stop_loss
    return candle.high >= signal.stop_loss


def _valid_candle(candle: OhlcCandle) -> bool:
    values = (candle.open, candle.high, candle.low, candle.close)
    if candle.high < candle.low:
        return False
    return candle.low <= candle.open <= candle.high and candle.low <= candle.close <= candle.high and all(value >= 0 for value in values)
