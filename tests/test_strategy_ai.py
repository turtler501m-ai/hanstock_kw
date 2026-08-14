import unittest
from unittest.mock import patch

import pandas as pd

from src.config import config
from src.strategy.features import MODEL_FEATURE_COLUMNS, build_strategy_features, feature_vector
from src.strategy.labels import buy_quality_label, future_return
from src.strategy.predict import ModelPredictor
from src.strategy.seven_split import find_candidates


class StrategyAiTests(unittest.TestCase):
    def test_feature_builder_returns_stable_vector_for_short_history(self):
        features = build_strategy_features([100.0, 101.0], strategy_score=2)

        self.assertEqual(features["feature_version"], "features_v1")
        self.assertEqual(len(feature_vector(features)), len(MODEL_FEATURE_COLUMNS))
        self.assertEqual(features["strategy_score"], 2.0)

    def test_buy_quality_label_applies_drawdown_penalty(self):
        prices = [100, 98, 104, 107, 109]

        self.assertAlmostEqual(future_return(prices, 0, 4), 0.09)
        self.assertEqual(
            buy_quality_label(prices, 0, horizon=4, min_return=0.03, drawdown_penalty=0.5),
            1,
        )

    def test_model_predictor_blends_rule_and_openai_score_when_enabled(self):
        with patch.object(config, "ai_strategy_enabled", True), patch.object(
            config, "openai_api_key", "test-key"
        ), patch.object(config, "openai_model", "gpt-5-mini"), patch.object(
            config, "ai_score_weight", 0.5
        ), patch.object(
            ModelPredictor, "_predict_probability", return_value=0.8
        ):
            predictor = ModelPredictor()
            result = predictor.predict({"strategy_score": 2.0, "feature_version": "features_v1"})

        self.assertEqual(result["model_status"], "ready")
        self.assertEqual(result["ml_score"], 0.8)
        self.assertAlmostEqual(result["final_score"], 3.0)
        self.assertEqual(result["provider"], "openai_responses")

    def test_find_candidates_exposes_ai_scoring_metadata_without_changing_default_score(self):
        dates = pd.date_range("2026-01-01", periods=80)
        df = pd.DataFrame(
            {
                "Close": [float(100 + i) for i in range(80)],
                "High": [float(101 + i) for i in range(80)],
                "Volume": [1000.0] * 79 + [2000.0],
            },
            index=dates,
        )

        with patch("src.strategy.seven_split.yf.download", return_value=df), patch.object(
            config, "ai_strategy_enabled", False
        ):
            result = find_candidates(held_symbols=set(), universe=["005930"], min_score=0)

        self.assertEqual(result["scanned"], 1)
        candidate = result["candidates"][0]
        self.assertIn("rule_score", candidate)
        self.assertIn("final_score", candidate)
        self.assertEqual(candidate["score"], candidate["rule_score"])
        self.assertEqual(candidate["ai_model_status"], "disabled")

    def test_model_predictor_falls_back_to_rule_score_when_below_min_confidence(self):
        # probability(0.5)가 min_ai_confidence(0.7) 미만 → AI를 신뢰하지 않고
        # 룰 점수로 폴백한다. final_score가 블렌딩되지 않아야 한다.
        with patch.object(config, "ai_strategy_enabled", True), patch.object(
            config, "openai_api_key", "test-key"
        ), patch.object(config, "openai_model", "gpt-5-mini"), patch.object(
            config, "ai_score_weight", 0.5
        ), patch.object(
            ModelPredictor, "_predict_probability", return_value=0.5
        ):
            predictor = ModelPredictor(strategy_profile={"min_ai_confidence": 0.7})
            result = predictor.predict({"strategy_score": 2.0, "feature_version": "features_v1"})

        self.assertEqual(result["model_status"], "low_confidence")
        self.assertEqual(result["ml_score"], 0.5)
        # 블렌딩 시 4.0이 되겠지만, 폴백이므로 rule_score 2.0이 그대로 유지된다.
        self.assertAlmostEqual(result["final_score"], 2.0)
        self.assertEqual(result["score_weight"], 0.0)
        self.assertIsNotNone(result["fallback_reason"])

    def test_model_predictor_falls_back_without_openai_api_key(self):
        with patch.object(config, "ai_strategy_enabled", True), patch.object(config, "openai_api_key", ""):
            predictor = ModelPredictor()
            result = predictor.predict({"strategy_score": 2.0, "feature_version": "features_v1"})

        self.assertEqual(result["model_status"], "fallback")
        self.assertEqual(result["fallback_reason"], "OPENAI_API_KEY not configured")


if __name__ == "__main__":
    unittest.main()
