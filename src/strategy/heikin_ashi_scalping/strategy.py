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
    """알파 하이킨아시 스캘핑 전략.

    영상의 여섯 가지 하이킨아시 매매법 중 마지막 핵심인 알파 하이킨아시를
    구현한다. 캔들을 두 번 평균 처리해 노이즈를 줄이고, 색상 전환을
    EMA 추세와 RSI 50선으로 확인한 뒤 전저점 손절과 2R-3R 목표가로
    리스크를 관리한다.
    """

    def __init__(
        self,
        fast_ema: int | None = None,
        slow_ema: int | None = None,
        rsi_period: int | None = None,
        min_score: float | None = None,
        volume_ratio: float | None = None,
    ) -> None:
        settings = self._load_settings()
        self.fast_ema = int(fast_ema if fast_ema is not None else settings["fast_ema"])
        self.slow_ema = int(slow_ema if slow_ema is not None else settings["slow_ema"])
        self.rsi_period = int(rsi_period if rsi_period is not None else settings["rsi_period"])
        self.min_score = float(min_score if min_score is not None else settings["min_score"])
        self.volume_ratio = float(volume_ratio if volume_ratio is not None else settings["volume_ratio"])

    def _load_settings(self) -> dict[str, float]:
        defaults = {
            "fast_ema": 10.0,
            "slow_ema": 20.0,
            "rsi_period": 14.0,
            "min_score": 3.5,
            "volume_ratio": 1.2,
        }
        try:
            from src.db.repository import get_watchlist_setting

            return {
                "fast_ema": float(get_watchlist_setting("HEIKIN_FAST_EMA", str(defaults["fast_ema"]))),
                "slow_ema": float(get_watchlist_setting("HEIKIN_SLOW_EMA", str(defaults["slow_ema"]))),
                "rsi_period": float(get_watchlist_setting("HEIKIN_RSI_PERIOD", str(defaults["rsi_period"]))),
                "min_score": float(get_watchlist_setting("HEIKIN_MIN_SCORE", str(defaults["min_score"]))),
                "volume_ratio": float(get_watchlist_setting("HEIKIN_VOLUME_RATIO", str(defaults["volume_ratio"]))),
            }
        except Exception:
            return defaults

    def calculate_score(self, prices: list[float], indicators: dict[str, Any]) -> float:
        if len(prices) < max(self.slow_ema + 3, self.rsi_period + 3, 30):
            indicators["custom_reasons"] = ["알파 하이킨아시 판단에 필요한 캔들 수가 부족합니다"]
            return 0.0

        candles = self._build_candles(prices, indicators)
        if len(candles) < 30:
            indicators["custom_reasons"] = ["OHLC 캔들 수가 부족합니다"]
            return 0.0

        ha = self._heikin_ashi(candles)
        alpha = self._heikin_ashi(ha)
        colors = [self._color(c) for c in alpha]
        last_color = colors[-1]
        prev_color = colors[-2]

        ema_fast = self._ema(prices, self.fast_ema)
        ema_slow = self._ema(prices, self.slow_ema)
        rsi_now = self._rsi(prices, self.rsi_period)
        rsi_prev = self._rsi(prices[:-1], self.rsi_period)
        current = prices[-1]

        score = 0.0
        reasons: list[str] = []

        if prev_color == "bear" and last_color == "bull":
            score += 2.0
            reasons.append("알파 하이킨아시가 하락색에서 상승색으로 전환")
        elif last_color == "bull" and self._recent_bear_to_bull(colors, lookback=4):
            score += 1.5
            reasons.append("최근 알파 하이킨아시 상승 반전 확인")
        elif last_color == "bull" and colors[-2] == "bull":
            score += 0.75
            reasons.append("알파 하이킨아시 상승 흐름 유지")

        if current > ema_slow and ema_fast >= ema_slow:
            score += 1.0
            reasons.append(f"가격이 EMA{self.slow_ema} 위, EMA{self.fast_ema}>={self.slow_ema}")
        elif current > ema_slow:
            score += 0.5
            reasons.append(f"가격이 EMA{self.slow_ema} 위")

        if self._doji_count(alpha[-5:-1]) >= 2 and last_color == "bull":
            score += 0.75
            reasons.append("도지 압축 이후 위쪽 방향 확정")

        regular_colors = [self._color(c) for c in ha]
        if regular_colors[-1] == "bull" and regular_colors[-2] == "bull":
            score += 0.75
            reasons.append("일반 하이킨아시 2연속 상승 확인")

        if rsi_prev <= 50.0 < rsi_now:
            score += 1.0
            reasons.append(f"RSI 50선 상향 돌파 ({rsi_prev:.1f}->{rsi_now:.1f})")
        elif 50.0 < rsi_now <= 65.0:
            score += 0.5
            reasons.append(f"RSI 50선 위 상승 모멘텀 ({rsi_now:.1f})")

        volumes = [float(v) for v in indicators.get("volumes", []) if v is not None]
        if len(volumes) >= 20:
            avg_vol = sum(volumes[-20:]) / 20
            if avg_vol > 0 and volumes[-1] >= avg_vol * self.volume_ratio:
                score += 0.5
                reasons.append("거래량이 반전 신호를 확인")

        risk = self._risk_plan(prices, alpha)
        indicators["custom_reasons"] = reasons or ["알파 하이킨아시 진입 조건 미충족"]
        indicators["heikin_ashi_scalping"] = {
            "alpha_color": last_color,
            "prev_alpha_color": prev_color,
            "ema_fast": round(ema_fast, 4),
            "ema_slow": round(ema_slow, 4),
            "rsi": round(rsi_now, 2),
            "entry": round(current, 4),
            "stop": risk["stop"],
            "target_2r": risk["target_2r"],
            "target_3r": risk["target_3r"],
            "score": round(min(5.0, score), 4),
        }
        return min(5.0, score)

    def _build_candles(self, prices: list[float], indicators: dict[str, Any]) -> list[Candle]:
        highs = [float(v) for v in indicators.get("highs", []) if v is not None]
        lows = [float(v) for v in indicators.get("lows", []) if v is not None]
        opens = [float(v) for v in indicators.get("opens", []) if v is not None]

        candles: list[Candle] = []
        for idx, close in enumerate(prices):
            prev_close = prices[idx - 1] if idx else close
            open_ = opens[idx] if idx < len(opens) else prev_close
            high = highs[idx] if idx < len(highs) else max(open_, close)
            low = lows[idx] if idx < len(lows) else min(open_, close)
            high = max(high, open_, close)
            low = min(low, open_, close)
            candles.append(Candle(float(open_), float(high), float(low), float(close)))
        return candles

    def _heikin_ashi(self, candles: list[Candle]) -> list[Candle]:
        result: list[Candle] = []
        prev_open = (candles[0].open + candles[0].close) / 2
        prev_close = (candles[0].open + candles[0].high + candles[0].low + candles[0].close) / 4
        for candle in candles:
            ha_close = (candle.open + candle.high + candle.low + candle.close) / 4
            ha_open = (prev_open + prev_close) / 2
            ha_high = max(candle.high, ha_open, ha_close)
            ha_low = min(candle.low, ha_open, ha_close)
            result.append(Candle(ha_open, ha_high, ha_low, ha_close))
            prev_open = ha_open
            prev_close = ha_close
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
        if not values:
            return 0.0
        alpha = 2 / (period + 1)
        ema = float(values[0])
        for value in values[1:]:
            ema = (float(value) * alpha) + (ema * (1 - alpha))
        return ema

    def _rsi(self, values: list[float], period: int) -> float:
        if len(values) < period + 1:
            return 50.0
        gains = 0.0
        losses = 0.0
        window = values[-(period + 1):]
        for idx in range(1, len(window)):
            delta = window[idx] - window[idx - 1]
            if delta >= 0:
                gains += delta
            else:
                losses += abs(delta)
        if losses == 0:
            return 100.0
        rs = gains / losses
        return 100 - (100 / (1 + rs))

    def _risk_plan(self, prices: list[float], alpha: list[Candle]) -> dict[str, float]:
        entry = prices[-1]
        swing_lows = [c.low for c in alpha[-8:-1]]
        stop = min(swing_lows) if swing_lows else min(prices[-8:-1], default=entry * 0.98)
        if stop >= entry:
            stop = entry * 0.98
        risk = entry - stop
        return {
            "stop": round(stop, 4),
            "target_2r": round(entry + (risk * 2), 4),
            "target_3r": round(entry + (risk * 3), 4),
        }
