import unittest

from src.strategy.narrative_momentum import NarrativeMomentumStrategy
from src.strategy.narrative_momentum_runner import build_summary


class NarrativeMomentumStrategyTest(unittest.TestCase):
    def setUp(self):
        self.theme_map = {
            "반도체": [
                {"ticker": "005930", "name": "삼성전자"},
                {"ticker": "000660", "name": "SK하이닉스"},
            ],
            "전력인프라": [
                {"ticker": "043260", "name": "HD현대일렉트릭"},
            ],
        }

    def test_calculates_signals_for_fresh_rising_narrative(self):
        history = [
            {
                "date": "2026-06-18",
                "market_mood": "risk_on",
                "dominant_narratives": [
                    {
                        "theme": "AI 반도체 투자 확대",
                        "strength": 88,
                        "sentiment": "bullish",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    }
                ],
                "narrative_shifts": [{"theme": "AI 반도체 투자 확대", "change": "rising"}],
            },
            {
                "date": "2026-06-17",
                "dominant_narratives": [
                    {
                        "theme": "AI 반도체 투자 확대",
                        "strength": 80,
                        "sentiment": "positive",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    }
                ],
            },
        ]
        signals = NarrativeMomentumStrategy().calculate_signals(history, self.theme_map, today_str="2026-06-18")
        tickers = {item["ticker"] for item in signals}
        self.assertIn("005930", tickers)
        self.assertIn("000660", tickers)
        self.assertGreaterEqual(signals[0]["final_score"], 75)
        self.assertIn("반도체", signals[0]["themes"])

    def test_stale_history_returns_empty_signals(self):
        history = [
            {
                "date": "2026-06-17",
                "dominant_narratives": [
                    {
                        "theme": "반도체 투자 확대",
                        "strength": 90,
                        "sentiment": "bullish",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    }
                ],
            }
        ]
        signals = NarrativeMomentumStrategy().calculate_signals(history, self.theme_map, today_str="2026-06-18")
        self.assertEqual(signals, [])

    def test_missing_history_status_keeps_static_dashboard_counts(self):
        status = NarrativeMomentumStrategy().status([], self.theme_map, today_str="2026-06-18")

        self.assertEqual(status["state"], "missing")
        self.assertEqual(status["narrative_count"], 0)
        self.assertEqual(status["shift_count"], 0)
        self.assertEqual(status["theme_count"], len(self.theme_map))
        self.assertEqual(status["approval_score_min"], 75.0)

    def test_weak_or_non_rising_narratives_are_filtered(self):
        history = [
            {
                "date": "2026-06-18",
                "dominant_narratives": [
                    {
                        "theme": "반도체 관망",
                        "strength": 69,
                        "sentiment": "bullish",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    },
                    {
                        "theme": "전력인프라 안정",
                        "strength": 90,
                        "sentiment": "bullish",
                        "direction": "stable",
                        "affected_sectors": ["전력인프라"],
                    },
                ],
            }
        ]
        signals = NarrativeMomentumStrategy().calculate_signals(history, self.theme_map, today_str="2026-06-18")
        self.assertEqual(signals, [])

    def test_multiple_theme_matches_accumulate_on_same_stock(self):
        theme_map = {
            "반도체": [{"ticker": "005930", "name": "삼성전자"}],
            "AI": [{"ticker": "005930", "name": "삼성전자"}],
        }
        history = [
            {
                "date": "2026-06-18",
                "dominant_narratives": [
                    {
                        "theme": "AI",
                        "strength": 80,
                        "sentiment": "bullish",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    }
                ],
            }
        ]
        signals = NarrativeMomentumStrategy().calculate_signals(history, theme_map, today_str="2026-06-18")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["ticker"], "005930")
        self.assertEqual(set(signals[0]["themes"]), {"AI", "반도체"})
        self.assertEqual(len(signals[0]["breakdown"]), 2)

    def test_runner_summary_reports_top_signals(self):
        summary = build_summary({
            "status": {"state": "fresh", "today": "2026-06-19", "latest_date": "2026-06-19"},
            "saved_count": 1,
            "unmatched": [{"theme": "미매칭"}],
            "signals": [
                {"ticker": "005930", "name": "삼성전자", "final_score": 90, "themes": ["반도체"]},
                {"ticker": "000660", "name": "SK하이닉스", "final_score": 80, "themes": ["반도체"]},
            ],
        })

        self.assertEqual(summary["state"], "fresh")
        self.assertEqual(summary["candidate_count"], 2)
        self.assertEqual(summary["saved_count"], 1)
        self.assertEqual(summary["unmatched_count"], 1)
        self.assertEqual(summary["avg_score"], 85.0)
        self.assertEqual(summary["top_signals"][0]["ticker"], "005930")


if __name__ == "__main__":
    unittest.main()
