"""Trend-filtered RSI oversold recovery strategy."""

from __future__ import annotations

from typing import Any


STRATEGY_PROFILE = {
    "strategy_type": "rebound",
    "risk_level": "balanced",
    "focus": [
        "price_above_rising_ema200",
        "rsi14_oversold_then_recovery",
        "previous_high_breakout",
        "volume_confirmation",
    ],
    "avoid": [
        "price_below_ema200",
        "falling_ema200",
        "support_breakdown",
        "high_volume_selloff",
    ],
    "market_regime_filter": ["bull"],
    "risk": {
        "max_risk_per_trade_pct": 1.0,
        "max_total_open_risk_pct": 2.0,
        "max_strategy_exposure_pct": 30.0,
    },
}


class CustomRSILimitStrategy:
    """RSI 과매도 반등 · EMA200 추세 확인형 평균회귀 전략.

    RSI 과매도 자체는 관찰 신호로만 사용합니다. 가격이 상승 중인 EMA200 위에
    있고 RSI(14)가 30 아래에서 다시 30을 돌파한 뒤 직전 봉 고점을 넘을 때만
    진입합니다. 최근 스윙 저점과 1.5 ATR로 손절선을 정하고 1R·2R 및 RSI70
    재이탈을 분할 청산 기준으로 제공합니다.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        buy_threshold: float = 35.0,
        ema_period: int = 200,
        ema_slope_lookback: int = 20,
        atr_period: int = 14,
        atr_stop_multiple: float = 1.5,
        volume_ratio: float = 1.0,
        recovery_window: int = 10,
        max_stop_distance_pct: float = 5.0,
        max_holding_bars: int = 20,
    ) -> None:
        self.rsi_period = int(rsi_period)
        self.buy_threshold = float(buy_threshold)
        self.ema_period = int(ema_period)
        self.ema_slope_lookback = int(ema_slope_lookback)
        self.atr_period = int(atr_period)
        self.atr_stop_multiple = float(atr_stop_multiple)
        self.volume_ratio = float(volume_ratio)
        self.recovery_window = int(recovery_window)
        self.max_stop_distance_pct = float(max_stop_distance_pct)
        self.max_holding_bars = int(max_holding_bars)

    def calculate_score(self, prices: list[float], indicators: dict[str, Any]) -> float:
        required = 500
        if len(prices) < required:
            indicators["custom_reasons"] = [f"EMA{self.ema_period} 추세 판단에 필요한 {required}봉이 부족합니다"]
            return 0.0

        candles = self._candles(prices, indicators)
        rsi_series = self._rsi_series(prices, self.rsi_period)
        ema_series = self._ema_series(prices, self.ema_period)
        current = float(prices[-1])
        previous_high = candles[-2][1]
        current_open = candles[-1][0]
        current_rsi = rsi_series[-1]
        previous_rsi = rsi_series[-2]
        ema_now = ema_series[-1]
        ema_past = ema_series[-1 - self.ema_slope_lookback]
        trend_ok = current > ema_now and ema_now > ema_past
        oversold_index = self._oversold_index(rsi_series)
        recovery_index = self._recovery_index(rsi_series)
        oversold_seen = oversold_index is not None
        rsi_recovered = recovery_index is not None and current_rsi > self.buy_threshold
        price_confirmed = current > previous_high and current > current_open

        volumes = [float(value) for value in indicators.get("volumes", []) if value is not None]
        volume_average = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 0.0
        volume_ratio = volumes[-1] / volume_average if volume_average > 0 else 0.0
        volume_confirmed = volume_ratio >= self.volume_ratio if volume_average > 0 else False
        gap_pct = (current_open / float(prices[-2]) - 1) * 100 if prices[-2] else 0.0
        event_risk = gap_pct <= -5.0 or (
            current < current_open and volume_ratio >= 1.5 and
            (current_open - current) / current_open * 100 >= 5.0
        )

        atr = self._atr(candles, self.atr_period)
        recent_swing_low = min(candle[2] for candle in candles[-8:-1])
        atr_stop = current - atr * self.atr_stop_multiple
        # Use the nearer of the confirmed swing low and ATR stop. Position sizing
        # still limits account risk, while an unnecessarily distant stop no
        # longer removes otherwise useful demo-account observations.
        stop = max(recent_swing_low, atr_stop)
        risk = max(current - stop, current * 0.001)
        bullish_divergence = self._bullish_divergence(prices, rsi_series)
        support_distance = current - recent_swing_low
        support_nearby = support_distance <= min(atr * 2.0, current * 0.03)
        stop_distance_pct = (current - stop) / current * 100 if current > 0 else 100.0
        risk_acceptable = 0 < stop_distance_pct <= self.max_stop_distance_pct
        symbol = str(indicators.get("symbol") or "")
        reentry_reset_ok = True
        if symbol:
            from src.strategy.position_tracker import allow_reentry_after_rsi_reset
            market = "US" if not symbol.isdigit() else "KR"
            reentry_reset_ok = allow_reentry_after_rsi_reset(
                market, symbol, "rsi_limit_strategy", current_rsi=current_rsi,
            )

        safety_ready = all((trend_ok, risk_acceptable, not event_risk, reentry_reset_ok))
        quality_score = (
            (2.0 if rsi_recovered else (0.75 if oversold_seen else 0.0))
            + (1.0 if price_confirmed else 0.0)
            + (0.5 if volume_confirmed else 0.0)
            + (0.5 if support_nearby else 0.0)
            + (0.5 if bullish_divergence else 0.0)
            + (0.5 if current_rsi > previous_rsi else 0.0)
        )
        score_components = {
            "rsi_recovery": 2.0 if rsi_recovered else (0.75 if oversold_seen else 0.0),
            "price_confirmation": 1.0 if price_confirmed else 0.0,
            "volume_confirmation": 0.5 if volume_confirmed else 0.0,
            "support_nearby": 0.5 if support_nearby else 0.0,
            "bullish_divergence": 0.5 if bullish_divergence else 0.0,
            "rsi_momentum": 0.5 if current_rsi > previous_rsi else 0.0,
        }
        signal_ready = safety_ready and rsi_recovered
        score = round(min(5.0, quality_score), 2) if signal_ready else 0.0
        entry_ready = safety_ready and score >= 2.0
        grade = "A" if entry_ready and score >= 4.0 else ("B" if entry_ready else "C")
        reasons = [
            f"EMA200 추세 {'통과' if trend_ok else '미통과'}",
            f"RSI(14) {previous_rsi:.1f}→{current_rsi:.1f} "
            f"{f'{self.buy_threshold:g} 재돌파' if rsi_recovered else '반등 대기'}",
            f"직전 고가 {previous_high:.2f} {'돌파' if price_confirmed else '미돌파'}",
            f"거래량 {volume_ratio:.2f}배 {'확인' if volume_confirmed else '선택 필터 미충족'}",
        ]
        metadata = {
            "phase": "entry" if entry_ready else ("setup" if trend_ok and oversold_seen else "filter"),
            "grade": grade,
            "entry_ready": entry_ready,
            "safety_ready": safety_ready,
            "signal_ready": signal_ready,
            "trend_ok": trend_ok,
            "oversold_seen": oversold_seen,
            "rsi_recovered": rsi_recovered,
            "price_confirmed": price_confirmed,
            "volume_confirmed": volume_confirmed,
            "bullish_divergence": bullish_divergence,
            "support_nearby": support_nearby,
            "support_distance_pct": round(support_distance / current * 100, 2) if current else 0.0,
            "risk_acceptable": risk_acceptable,
            "event_risk": event_risk,
            "gap_pct": round(gap_pct, 2),
            "reentry_reset_ok": reentry_reset_ok,
            "stop_distance_pct": round(stop_distance_pct, 2),
            "recovery_index_bars_ago": len(prices) - 1 - recovery_index if recovery_index is not None else None,
            "oversold_index_bars_ago": len(prices) - 1 - oversold_index if oversold_index is not None else None,
            "rsi": round(current_rsi, 2),
            "previous_rsi": round(previous_rsi, 2),
            "ema200": round(ema_now, 4),
            "ema200_20_bars_ago": round(ema_past, 4),
            "previous_high": round(previous_high, 4),
            "atr": round(atr, 4),
            "recent_swing_low": round(recent_swing_low, 4),
            "stop": round(stop, 4),
            "risk_per_share": round(risk, 4),
            "target_1r": round(current + risk, 4),
            "target_2r": round(current + risk * 2, 4),
            "runner_exit": previous_rsi >= 70 > current_rsi,
            "reentry_allowed": entry_ready,
            "score": score,
            "score_components": score_components,
            "minimum_entry_score": 2.0,
            "strategy_version": "rsi_pullback_recovery_v3_demo_scored",
            "effective_parameters": self.effective_config(),
        }
        indicators["custom_reasons"] = reasons
        indicators["rsi_oversold_rebound"] = metadata
        return metadata["score"]

    def effective_config(self) -> dict[str, float | int | str]:
        return {
            "timeframe": "daily_closed_bar",
            "minimum_history": 500,
            "ema_period": self.ema_period,
            "ema_slope_bars": self.ema_slope_lookback,
            "rsi_period": self.rsi_period,
            "oversold_threshold": self.buy_threshold,
            "recovery_trigger_window_bars": self.recovery_window,
            "atr_period": self.atr_period,
            "atr_stop_multiple": self.atr_stop_multiple,
            "max_stop_distance_pct": self.max_stop_distance_pct,
            "risk_per_trade_pct": 1.0,
            "max_exposure_pct": 30.0,
            "max_holding_bars": self.max_holding_bars,
            "reentry_reset_rsi": 50.0,
        }

    def _recovery_index(self, rsi_values: list[float]) -> int | None:
        start = max(1, len(rsi_values) - self.recovery_window - 1)
        for index in range(len(rsi_values) - 1, start - 1, -1):
            if rsi_values[index - 1] <= self.buy_threshold < rsi_values[index]:
                return index
        return None

    def _oversold_index(self, rsi_values: list[float]) -> int | None:
        start = max(0, len(rsi_values) - self.recovery_window - 1)
        for index in range(len(rsi_values) - 2, start - 1, -1):
            if rsi_values[index] <= self.buy_threshold:
                return index
        return None

    def _candles(self, prices: list[float], indicators: dict[str, Any]) -> list[tuple[float, float, float, float]]:
        opens = indicators.get("opens") or []
        highs = indicators.get("highs") or []
        lows = indicators.get("lows") or []
        result = []
        for index, close_value in enumerate(prices):
            close = float(close_value)
            previous = float(prices[index - 1]) if index else close
            open_ = float(opens[index]) if index < len(opens) else previous
            high = float(highs[index]) if index < len(highs) else max(open_, close)
            low = float(lows[index]) if index < len(lows) else min(open_, close)
            result.append((open_, max(high, open_, close), min(low, open_, close), close))
        return result

    def _ema_series(self, values: list[float], period: int) -> list[float]:
        smoothing = 2 / (period + 1)
        ema = float(values[0])
        result = [ema]
        for value in values[1:]:
            ema = float(value) * smoothing + ema * (1 - smoothing)
            result.append(ema)
        return result

    def _rsi_series(self, values: list[float], period: int) -> list[float]:
        result = [50.0] * len(values)
        for index in range(period, len(values)):
            window = values[index - period:index + 1]
            gains = sum(max(float(current) - float(previous), 0.0) for previous, current in zip(window, window[1:]))
            losses = sum(max(float(previous) - float(current), 0.0) for previous, current in zip(window, window[1:]))
            result[index] = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
        return result

    def _atr(self, candles: list[tuple[float, float, float, float]], period: int) -> float:
        ranges = [
            max(current[1] - current[2], abs(current[1] - previous[3]), abs(current[2] - previous[3]))
            for previous, current in zip(candles, candles[1:])
        ]
        window = ranges[-period:]
        return sum(window) / len(window) if window else 0.0

    def _bullish_divergence(self, prices: list[float], rsi_values: list[float]) -> bool:
        # A pivot is usable only after two right-side bars have closed.  This
        # keeps the signal causal in both live evaluation and backtests.
        pivots = []
        start = max(2, len(prices) - 30)
        for index in range(start, len(prices) - 2):
            window = prices[index - 2:index + 3]
            if prices[index] == min(window):
                if not pivots or index - pivots[-1] >= 3:
                    pivots.append(index)
        if len(pivots) < 2:
            return False
        older, newer = pivots[-2:]
        return prices[newer] < prices[older] and rsi_values[newer] > rsi_values[older]
