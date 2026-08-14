# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from src import trader

STRATEGY_ID = "narrative_momentum_strategy"
DAY_WEIGHTS = [1.0, 0.6, 0.3]
SENTIMENT_MULT = {
    "bullish": 1.2,
    "positive": 1.2,
    "neutral": 1.0,
    "mixed": 0.8,
    "negative": 0.5,
    "bearish": 0.5,
}
SHIFT_BONUS = {
    "new": 15.0,
    "rising": 5.0,
    "fading": -10.0,
    "disappeared": -10.0,
}


@dataclass(frozen=True)
class NarrativeMomentumSettings:
    strength_min: float = 70.0
    theme_match_threshold: float = 0.45
    streak_match_threshold: float = 0.55
    approval_score_min: float = 75.0


class NarrativeMomentumStrategy:
    """뉴스 내러티브를 테마-종목 점수로 변환하는 독립 전략 엔진."""

    def __init__(self, settings: NarrativeMomentumSettings | None = None) -> None:
        self.settings = settings or NarrativeMomentumSettings()

    def calculate_signals(
        self,
        narrative_history: list[dict[str, Any]],
        theme_map: dict[str, list[dict[str, Any]]],
        today_str: str | None = None,
    ) -> list[dict[str, Any]]:
        entries = _normalize_history(narrative_history)[:3]
        if not entries or not theme_map:
            return []

        today = today_str or datetime.now(trader.KST).strftime("%Y-%m-%d")
        latest_date = str(entries[0].get("date") or "")
        if latest_date != today:
            return []

        theme_keys = list(theme_map.keys())
        today_entry = entries[0]
        shift_bonus = {
            str(item.get("theme") or ""): SHIFT_BONUS.get(str(item.get("change") or ""), 0.0)
            for item in today_entry.get("narrative_shifts", []) or []
            if isinstance(item, dict)
        }
        scores: dict[str, dict[str, Any]] = {}

        for day_index, entry in enumerate(entries):
            day_weight = DAY_WEIGHTS[day_index] if day_index < len(DAY_WEIGHTS) else 0.2
            prev_narratives = entries[day_index + 1].get("dominant_narratives", []) if day_index + 1 < len(entries) else []

            for narrative in entry.get("dominant_narratives", []) or []:
                if not isinstance(narrative, dict):
                    continue
                strength = _to_float(narrative.get("strength"))
                direction = str(narrative.get("direction") or "").lower()
                if direction != "rising" or strength <= self.settings.strength_min:
                    continue

                theme = str(narrative.get("theme") or "").strip()
                if not theme:
                    continue

                delta = self._strength_delta(theme, strength, prev_narratives)
                sentiment = str(narrative.get("sentiment") or "neutral").lower()
                sentiment_mult = SENTIMENT_MULT.get(sentiment, 1.0)
                acceleration = self._acceleration(theme, entries) if day_index == 0 else 1.0
                bonus = self._shift_bonus(theme, shift_bonus) if day_index == 0 else 0.0
                narrative_score = (strength * (1 + delta / 100.0) * day_weight * sentiment_mult * acceleration) + bonus
                matched_themes = self._matched_themes(narrative, theme_keys)

                for matched_theme, match_score in matched_themes:
                    matched_score = narrative_score * match_score
                    for stock in theme_map.get(matched_theme, []) or []:
                        if not isinstance(stock, dict):
                            continue
                        ticker = str(stock.get("ticker") or stock.get("symbol") or "").strip()
                        if not ticker:
                            continue
                        item = scores.setdefault(
                            ticker,
                            {
                                "ticker": ticker,
                                "symbol": ticker,
                                "name": str(stock.get("name") or ticker),
                                "score": 0.0,
                                "rule_score": 0.0,
                                "ml_score": None,
                                "final_score": 0.0,
                                "current_price": 0,
                                "themes": [],
                                "narratives": [],
                                "reasons": [],
                                "strategy_id": STRATEGY_ID,
                                "breakdown": [],
                            },
                        )
                        item["score"] += matched_score
                        item["rule_score"] = item["score"]
                        item["final_score"] = item["score"]
                        if matched_theme not in item["themes"]:
                            item["themes"].append(matched_theme)
                        label = f"{theme}({strength:g},{sentiment})"
                        if label not in item["narratives"]:
                            item["narratives"].append(label)
                        reason = (
                            f"내러티브: {theme} strength={strength:g} sentiment={sentiment} "
                            f"direction={direction} accel={acceleration:g}"
                        )
                        if reason not in item["reasons"]:
                            item["reasons"].append(reason)
                        item["breakdown"].append(
                            {
                                "date": entry.get("date"),
                                "theme": theme,
                                "matched_theme": matched_theme,
                                "match_score": round(match_score, 4),
                                "strength": strength,
                                "delta": round(delta, 4),
                                "day_weight": day_weight,
                                "sentiment": sentiment,
                                "sentiment_mult": sentiment_mult,
                                "acceleration": acceleration,
                                "shift_bonus": bonus,
                                "score": round(matched_score, 4),
                                "raw_score": round(narrative_score, 4),
                            }
                        )

        results = []
        for item in scores.values():
            final_score = max(0.0, min(100.0, round(float(item["final_score"]), 1)))
            item["score"] = final_score
            item["rule_score"] = final_score
            item["final_score"] = final_score
            results.append(item)
        return sorted(results, key=lambda row: (-float(row["final_score"]), row["ticker"]))

    def status(
        self,
        narrative_history: list[dict[str, Any]],
        theme_map: dict[str, list[dict[str, Any]]],
        today_str: str | None = None,
    ) -> dict[str, Any]:
        entries = _normalize_history(narrative_history)
        today = today_str or datetime.now(trader.KST).strftime("%Y-%m-%d")
        base = {
            "today": today,
            "theme_count": len(theme_map),
            "approval_score_min": self.settings.approval_score_min,
        }
        if not entries:
            return {
                **base,
                "state": "missing",
                "latest_date": None,
                "market_mood": None,
                "mood_score": None,
                "narrative_count": 0,
                "shift_count": 0,
                "message": "narrative_history is empty",
            }
        latest = entries[0]
        latest_date = str(latest.get("date") or "")
        state = "fresh" if latest_date == today else "stale"
        return {
            **base,
            "state": state,
            "latest_date": latest_date,
            "market_mood": latest.get("market_mood"),
            "mood_score": latest.get("mood_score"),
            "narrative_count": len(latest.get("dominant_narratives", []) or []),
            "shift_count": len(latest.get("narrative_shifts", []) or []),
        }

    def unmatched_narratives(
        self,
        narrative_history: list[dict[str, Any]],
        theme_map: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        entries = _normalize_history(narrative_history)[:3]
        theme_keys = list(theme_map.keys())
        unmatched = []
        for entry in entries:
            for narrative in entry.get("dominant_narratives", []) or []:
                if not isinstance(narrative, dict):
                    continue
                matched = self._matched_themes(narrative, theme_keys)
                if not matched:
                    unmatched.append(
                        {
                            "date": entry.get("date"),
                            "theme": narrative.get("theme"),
                            "affected_sectors": narrative.get("affected_sectors", []),
                            "strength": narrative.get("strength"),
                            "sentiment": narrative.get("sentiment"),
                            "direction": narrative.get("direction"),
                        }
                    )
        return unmatched

    def _strength_delta(self, theme: str, strength: float, prev_narratives: list[Any]) -> float:
        for prev in prev_narratives or []:
            if not isinstance(prev, dict):
                continue
            if _sim(theme, str(prev.get("theme") or "")) >= self.settings.streak_match_threshold:
                return strength - _to_float(prev.get("strength"))
        return 0.0

    def _acceleration(self, theme: str, entries: list[dict[str, Any]]) -> float:
        streak = 0
        for entry in entries:
            hit = None
            for narrative in entry.get("dominant_narratives", []) or []:
                if isinstance(narrative, dict) and _sim(theme, str(narrative.get("theme") or "")) >= self.settings.streak_match_threshold:
                    hit = narrative
                    break
            if hit and str(hit.get("direction") or "").lower() == "rising":
                streak += 1
            else:
                break
        if streak >= 3:
            return 1.25
        if streak >= 2:
            return 1.10
        return 1.0

    def _shift_bonus(self, theme: str, shift_bonus: dict[str, float]) -> float:
        for shift_theme, bonus in shift_bonus.items():
            if _sim(theme, shift_theme) >= self.settings.streak_match_threshold:
                return bonus
        return 0.0

    def _matched_themes(self, narrative: dict[str, Any], theme_keys: list[str]) -> list[tuple[str, float]]:
        candidates = [str(narrative.get("theme") or "")]
        candidates.extend(str(item) for item in narrative.get("affected_sectors", []) or [])
        matches: dict[str, float] = {}
        for candidate in candidates:
            for key in theme_keys:
                score = _sim(candidate, key)
                if score >= self.settings.theme_match_threshold:
                    matches[key] = max(matches.get(key, 0.0), score)
        return sorted(matches.items(), key=lambda item: (-item[1], item[0]))


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(history, list):
        return []
    entries = [item for item in history if isinstance(item, dict)]
    return sorted(entries, key=lambda item: str(item.get("date") or ""), reverse=True)


def load_json_file(path: str | Path, default: Any) -> Any:
    file_path = Path(path)
    if not file_path.exists():
        return default
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json_file(path: str | Path, payload: Any) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
