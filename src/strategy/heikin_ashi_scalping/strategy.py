from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float


class HeikinAshiScalpingStrategy:
    """Alpha Heikin-Ashi confirmation strategy with an EMA200 regime filter."""

    def __init__(
        self,
        fast_ema: int | None = None,
        slow_ema: int | None = None,
        rsi_period: int | None = None,
        min_score: float | None = None,
        volume_ratio: float | None = None,
        trend_ema: int | None = None,
        min_adx: float | None = None,
        min_atr_pct: float | None = None,
        max_atr_pct: float | None = None,
        max_stop_distance_pct: float | None = None,
        max_entry_premium_pct: float | None = None,
        ema_slope_lookback: int | None = None,
        trigger_window: int = 7,
    ) -> None:
        settings = self._load_settings()
        self.fast_ema = int(fast_ema if fast_ema is not None else settings["fast_ema"])
        self.slow_ema = int(slow_ema if slow_ema is not None else settings["slow_ema"])
        self.rsi_period = int(rsi_period if rsi_period is not None else settings["rsi_period"])
        self.min_score = float(min_score if min_score is not None else settings["min_score"])
        self.volume_ratio = float(volume_ratio if volume_ratio is not None else settings["volume_ratio"])
        v2 = self._load_v2_settings()
        self.trend_ema = int(trend_ema if trend_ema is not None else v2["trend_ema"])
        self.min_adx = float(min_adx if min_adx is not None else v2["min_adx"])
        self.min_atr_pct = float(min_atr_pct if min_atr_pct is not None else v2["min_atr_pct"])
        self.max_atr_pct = float(max_atr_pct if max_atr_pct is not None else v2["max_atr_pct"])
        self.max_stop_distance_pct = float(
            max_stop_distance_pct
            if max_stop_distance_pct is not None
            else v2["max_stop_distance_pct"]
        )
        self.max_entry_premium_pct = float(
            max_entry_premium_pct
            if max_entry_premium_pct is not None
            else v2["max_entry_premium_pct"]
        )
        self.ema_slope_lookback = int(ema_slope_lookback if ema_slope_lookback is not None else v2["ema_slope_lookback"])
        self.trigger_window = int(trigger_window)

    def _load_settings(self) -> dict[str, float]:
        defaults = {"fast_ema": 10.0, "slow_ema": 20.0, "rsi_period": 14.0, "min_score": 2.5, "volume_ratio": 1.2}
        try:
            from src.db.repository import get_watchlist_setting

            return {
                key: float(get_watchlist_setting(db_key, str(defaults[key])))
                for key, db_key in {
                    "fast_ema": "HEIKIN_FAST_EMA",
                    "slow_ema": "HEIKIN_SLOW_EMA",
                    "rsi_period": "HEIKIN_RSI_PERIOD",
                    "min_score": "HEIKIN_MIN_SCORE",
                    "volume_ratio": "HEIKIN_VOLUME_RATIO",
                }.items()
            }
        except Exception:
            return defaults

    def _load_v2_settings(self) -> dict[str, float]:
        defaults = {
            "trend_ema": 200.0, "ema_slope_lookback": 20.0,
            "min_adx": 20.0, "min_atr_pct": 0.5, "max_atr_pct": 5.0,
            "max_stop_distance_pct": 8.0, "max_entry_premium_pct": 2.0,
        }
        try:
            from src.db.repository import get_watchlist_setting
            return {
                key: float(get_watchlist_setting(db_key, str(defaults[key])))
                for key, db_key in {
                    "trend_ema": "ALPHA_HA_EMA_PERIOD",
                    "ema_slope_lookback": "ALPHA_HA_EMA_SLOPE_BARS",
                    "min_adx": "ALPHA_HA_ADX_MIN",
                    "min_atr_pct": "ALPHA_HA_ATR_PCT_MIN",
                    "max_atr_pct": "ALPHA_HA_ATR_PCT_MAX",
                    "max_stop_distance_pct": "ALPHA_HA_MAX_STOP_DISTANCE_PCT",
                    "max_entry_premium_pct": "ALPHA_HA_MAX_ENTRY_PREMIUM_PCT",
                }.items()
            }
        except Exception:
            return defaults

    def calculate_score(self, prices: list[float], indicators: dict[str, Any]) -> float:
        if len(prices) < 500:
            indicators["custom_reasons"] = [f"EMA{self.trend_ema} 판단에 필요한 캔들이 부족합니다"]
            return 0.0

        candles = self._build_candles(prices, indicators)
        alpha = self._heikin_ashi(self._heikin_ashi(candles))
        colors = [self._color(candle) for candle in alpha]
        current = float(prices[-1])
        ema_series = self._ema_series(prices, self.trend_ema)
        ema_now = ema_series[-1]
        ema_previous = ema_series[-1 - self.ema_slope_lookback]
        fast_ema = self._ema(prices, self.fast_ema)
        slow_ema = self._ema(prices, self.slow_ema)
        fast_trend_ok = current > fast_ema >= slow_ema
        current_rsi = self._rsi(prices, self.rsi_period)
        previous_rsi = self._rsi(prices[:-1], self.rsi_period)
        rsi_momentum_ok = current_rsi >= 50 and current_rsi >= previous_rsi

        volumes = [float(value) for value in indicators.get("volumes", []) if value is not None]
        volume_average = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 0.0
        current_volume_ratio = volumes[-1] / volume_average if volume_average > 0 else 0.0
        volume_confirmed = current_volume_ratio >= self.volume_ratio if volume_average > 0 else False

        atr = self._atr(candles, 14)
        atr_pct = atr / current * 100 if current > 0 else 0.0
        adx, plus_di, minus_di = self._directional_indicators(candles, 14)
        volatility_ok = self.min_atr_pct <= atr_pct <= self.max_atr_pct
        trend_quality_ok = adx >= self.min_adx and plus_di > minus_di
        reversal_index = self._confirmed_reversal_index(colors, "long")
        short_reversal_index = self._confirmed_reversal_index(colors, "short")
        long_trigger = reversal_index is not None and current > candles[reversal_index].high
        short_trigger = short_reversal_index is not None and current < candles[short_reversal_index].low
        alpha_reversal = reversal_index is not None
        trend_ok = current > ema_now and ema_now > ema_previous
        risk = self._risk_plan(prices, candles)
        stop_distance_pct = (current - risk["stop"]) / current * 100 if current > 0 else 100.0
        risk_acceptable = 0 < stop_distance_pct <= self.max_stop_distance_pct
        gap_pct = (candles[-1].open / prices[-2] - 1) * 100 if prices[-2] else 0.0
        event_risk = gap_pct <= -5.0 or (
            current < candles[-1].open and current_volume_ratio >= 1.5 and
            (candles[-1].open - current) / candles[-1].open * 100 >= 5.0
        )
        safety_ready = all((trend_ok, volatility_ok, risk_acceptable, not event_risk))
        score_components = {
            "alpha_reversal": 1.5 if alpha_reversal else 0.0,
            "price_confirmation": 1.0 if long_trigger else 0.0,
            "adx_quality": 0.75 if adx >= self.min_adx else 0.0,
            "directional_strength": 0.5 if plus_di > minus_di else 0.0,
            "ema10_20_trend": 0.5 if fast_trend_ok else 0.0,
            "rsi_momentum": 0.5 if rsi_momentum_ok else 0.0,
            "volume_confirmation": 0.25 if volume_confirmed else 0.0,
        }
        quality_score = round(min(5.0, sum(score_components.values())), 2)
        score = quality_score if safety_ready and alpha_reversal and quality_score >= 2.5 else 0.0
        long_setup = score >= 2.5
        short_setup = all((
            current < ema_now,
            ema_now < ema_previous,
            short_trigger,
            adx >= self.min_adx and minus_di > plus_di,
            volatility_ok,
        ))

        direction = "long" if long_setup else ("short" if short_setup else "flat")
        if long_setup:
            reasons = [
                "Alpha HA 음→양 전환 확인",
                f"진입 품질 {score:.2f}/5.00점 (기준 2.50점)",
                f"신호봉 고점 {'돌파' if long_trigger else '미돌파(가점 없음)'}",
            ]
        elif short_setup:
            reasons = ["Alpha HA 양→음 전환 후 다음 음봉 확인", "하락 중인 EMA200 아래에서 신호봉 저점 이탈", "현물 전략에서는 SHORT 주문을 생성하지 않음"]
        else:
            reasons = [
                "Alpha HA 점수형 진입 조건 미충족",
                f"안전필터 {'통과' if safety_ready else '미통과'}, 반전 {'확인' if alpha_reversal else '없음'}, 품질 {quality_score:.2f}/5.00점",
            ]
        reasons.append(f"변동성 확인 ADX={adx:.1f}, ATR={atr_pct:.2f}%")

        metadata = {
            "alpha_color": colors[-1],
            "prev_alpha_color": colors[-2],
            "direction": direction,
            "long_setup": long_setup,
            "entry_ready": long_setup,
            "safety_ready": safety_ready,
            "trend_ok": trend_ok,
            "alpha_reversal": alpha_reversal,
            "price_confirmed": long_trigger,
            "trend_quality_ok": trend_quality_ok,
            "fast_trend_ok": fast_trend_ok,
            "rsi_momentum_ok": rsi_momentum_ok,
            "volume_confirmed": volume_confirmed,
            "risk_acceptable": risk_acceptable,
            "event_risk": event_risk,
            "short_setup": short_setup,
            "ema200": round(ema_now, 4),
            "ema200_slope": round(ema_now - ema_previous, 4),
            "ema200_slope_pct": round((ema_now / ema_previous - 1) * 100, 4) if ema_previous else 0.0,
            "adx": round(adx, 2),
            "plus_di": round(plus_di, 2),
            "minus_di": round(minus_di, 2),
            "atr": round(atr, 4),
            "atr_pct": round(atr_pct, 2),
            "volatility_ok": volatility_ok,
            "rsi": round(current_rsi, 2),
            "previous_rsi": round(previous_rsi, 2),
            "fast_ema": round(fast_ema, 4),
            "slow_ema": round(slow_ema, 4),
            "volume_ratio": round(current_volume_ratio, 2),
            "gap_pct": round(gap_pct, 2),
            "stop_distance_pct": round(stop_distance_pct, 2),
            "signal_high": round(candles[reversal_index].high, 4) if reversal_index is not None else None,
            "signal_low": round(candles[short_reversal_index].low, 4) if short_reversal_index is not None else None,
            "exit_long": colors[-1] == "bear",
            "exit_long_confirmed": colors[-2:] == ["bear", "bear"],
            "exit_short": colors[-1] == "bull",
            "entry": round(current, 4),
            **risk,
            "score": score,
            "score_components": score_components,
            "minimum_entry_score": 2.5,
            "strategy_version": "alpha_ha_pullback_v3_demo_scored",
            "effective_parameters": self.effective_config(),
        }
        indicators["custom_reasons"] = reasons
        indicators["heikin_ashi_scalping"] = metadata
        return metadata["score"]

    def effective_config(self) -> dict[str, float | int | str]:
        return {
            "timeframe": "daily_closed_bar",
            "minimum_history": 500,
            "ema_period": self.trend_ema,
            "ema_slope_bars": self.ema_slope_lookback,
            "ha_smoothing_passes": 2,
            "trigger_window_bars": self.trigger_window,
            "fast_ema_period": self.fast_ema,
            "slow_ema_period": self.slow_ema,
            "rsi_period": self.rsi_period,
            "volume_ratio_min": self.volume_ratio,
            "minimum_entry_score": 2.5,
            "adx_period": 14,
            "adx_min": self.min_adx,
            "atr_period": 14,
            "atr_pct_min": self.min_atr_pct,
            "atr_pct_max": self.max_atr_pct,
            "max_stop_distance_pct": self.max_stop_distance_pct,
            "max_entry_premium_pct": self.max_entry_premium_pct,
        }

    def _confirmed_reversal_index(self, colors: list[str], direction: str) -> int | None:
        reversal = ("bear", "bull") if direction == "long" else ("bull", "bear")
        continuation = reversal[1]
        start = max(1, len(colors) - self.trigger_window - 2)
        for index in range(len(colors) - 2, start - 1, -1):
            if colors[index - 1:index + 1] != list(reversal):
                continue
            confirmation = index + 1
            if confirmation >= len(colors) or colors[confirmation] != continuation:
                continue
            if all(color == continuation for color in colors[confirmation:]):
                return index
        return None

    def _build_candles(self, prices: list[float], indicators: dict[str, Any]) -> list[Candle]:
        highs = [float(value) for value in indicators.get("highs", []) if value is not None]
        lows = [float(value) for value in indicators.get("lows", []) if value is not None]
        opens = [float(value) for value in indicators.get("opens", []) if value is not None]
        candles = []
        for index, close_value in enumerate(prices):
            close = float(close_value)
            previous_close = float(prices[index - 1]) if index else close
            open_ = opens[index] if index < len(opens) else previous_close
            high = highs[index] if index < len(highs) else max(open_, close)
            low = lows[index] if index < len(lows) else min(open_, close)
            candles.append(Candle(open_, max(high, open_, close), min(low, open_, close), close))
        return candles

    def _heikin_ashi(self, candles: list[Candle]) -> list[Candle]:
        result = []
        previous_open = (candles[0].open + candles[0].close) / 2
        previous_close = sum((candles[0].open, candles[0].high, candles[0].low, candles[0].close)) / 4
        for candle in candles:
            close = sum((candle.open, candle.high, candle.low, candle.close)) / 4
            open_ = (previous_open + previous_close) / 2
            result.append(Candle(open_, max(candle.high, open_, close), min(candle.low, open_, close), close))
            previous_open, previous_close = open_, close
        return result

    def _color(self, candle: Candle) -> str:
        if abs(candle.close - candle.open) <= self._range(candle) * 0.12:
            return "doji"
        return "bull" if candle.close > candle.open else "bear"

    def _range(self, candle: Candle) -> float:
        return max(candle.high - candle.low, abs(candle.close) * 0.0001, 1e-9)

    def _doji_count(self, candles: list[Candle]) -> int:
        return sum(1 for candle in candles if self._color(candle) == "doji")

    def _recent_bear_to_bull(self, colors: list[str], lookback: int) -> bool:
        window = colors[-lookback:]
        return "bear" in window[:-1] and window[-1] == "bull"

    def _ema(self, values: list[float], period: int) -> float:
        series = self._ema_series(values, period)
        return series[-1] if series else 0.0

    def _ema_series(self, values: list[float], period: int) -> list[float]:
        if not values:
            return []
        smoothing = 2 / (period + 1)
        ema = float(values[0])
        result = [ema]
        for value in values[1:]:
            ema = float(value) * smoothing + ema * (1 - smoothing)
            result.append(ema)
        return result

    def _rsi(self, values: list[float], period: int) -> float:
        if len(values) < period + 1:
            return 50.0
        gains = losses = 0.0
        window = values[-(period + 1):]
        for previous, current in zip(window, window[1:]):
            delta = current - previous
            gains += max(delta, 0)
            losses += max(-delta, 0)
        if losses == 0:
            return 100.0
        relative_strength = gains / losses
        return 100 - 100 / (1 + relative_strength)

    def _atr(self, candles: list[Candle], period: int) -> float:
        ranges = [max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)) for previous, current in zip(candles, candles[1:])]
        window = ranges[-period:]
        return sum(window) / len(window) if window else 0.0

    def _adx(self, candles: list[Candle], period: int) -> float:
        return self._directional_indicators(candles, period)[0]

    def _directional_indicators(self, candles: list[Candle], period: int) -> tuple[float, float, float]:
        if len(candles) < period + 1:
            return 0.0, 0.0, 0.0
        plus_dm, minus_dm, ranges = [], [], []
        for previous, current in zip(candles, candles[1:]):
            up_move, down_move = current.high - previous.high, previous.low - current.low
            plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
            ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
        true_range = sum(ranges[-period:])
        if true_range <= 0:
            return 0.0, 0.0, 0.0
        plus_di = 100 * sum(plus_dm[-period:]) / true_range
        minus_di = 100 * sum(minus_dm[-period:]) / true_range
        total = plus_di + minus_di
        adx = 100 * abs(plus_di - minus_di) / total if total > 0 else 0.0
        return adx, plus_di, minus_di

    def _risk_plan(self, prices: list[float], candles: list[Candle]) -> dict[str, float]:
        entry = float(prices[-1])
        swing_lows = [candle.low for candle in candles[-8:-1]]
        stop = min(swing_lows) if swing_lows else min(prices[-8:-1], default=entry * 0.98)
        if stop >= entry:
            stop = entry * 0.98
        risk = entry - stop
        return {"stop": round(stop, 4), "target_1r": round(entry + risk, 4), "target_2r": round(entry + risk * 2, 4), "target_3r": round(entry + risk * 3, 4)}
