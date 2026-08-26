from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.market_regime.policy import evaluate_new_risk, expand_allowed_regimes


def snapshot(**changes):
    value = {
        "regime": "sideways_low_vol",
        "quality": "degraded",
        "risk_multiplier": 0.5,
        "new_risk_allowed": True,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    value.update(changes)
    return value


class MarketRegimePolicyTests(unittest.TestCase):
    def test_legacy_vocabulary_expands_to_canonical_regimes(self):
        expanded = expand_allowed_regimes(["neutral", "low_volatility"])
        self.assertIn("sideways_low_vol", expanded)
        self.assertIn("bull_pullback", expanded)

    def test_degraded_allowed_regime_reduces_new_risk(self):
        result = evaluate_new_risk(snapshot(), ["neutral"])
        self.assertTrue(result.allowed)
        self.assertEqual(result.multiplier, 0.5)

    def test_strategy_regime_mismatch_blocks_new_risk(self):
        result = evaluate_new_risk(snapshot(), ["bull"])
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "market_regime_not_allowed")

    def test_regime_caps_apply_even_when_strategy_allows_it(self):
        high_vol = evaluate_new_risk(
            snapshot(regime="sideways_high_vol", quality="good", risk_multiplier=1.0),
            ["sideways_high_vol"],
        )
        bear = evaluate_new_risk(
            snapshot(regime="bear", quality="good", risk_multiplier=1.0),
            ["bear"],
        )
        self.assertTrue(high_vol.allowed)
        self.assertEqual(high_vol.multiplier, 0.4)
        self.assertFalse(bear.allowed)
        self.assertEqual(bear.reason, "market_regime_zero_risk")

    def test_strategy_configured_max_percent_is_applied(self):
        current = snapshot(
            regime="sideways_high_vol", quality="good", risk_multiplier=0.9
        )
        result = evaluate_new_risk(
            current,
            ["sideways_high_vol"],
            {"sideways_high_vol": 25},
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.multiplier, 0.25)

        blocked = evaluate_new_risk(
            current,
            ["sideways_high_vol"],
            {"sideways_high_vol": 0},
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "market_regime_zero_risk")

    def test_missing_insufficient_and_stale_snapshots_fail_closed(self):
        stale = snapshot(evaluated_at="2020-01-01T00:00:00+00:00")
        insufficient = snapshot(quality="insufficient", new_risk_allowed=False)
        self.assertEqual(evaluate_new_risk(None, ["bull"]).multiplier, 0.0)
        self.assertEqual(evaluate_new_risk(stale, ["neutral"]).reason, "market_regime_stale")
        self.assertEqual(evaluate_new_risk(insufficient, ["neutral"]).reason, "market_regime_insufficient")


if __name__ == "__main__":
    unittest.main()
