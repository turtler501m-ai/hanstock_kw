from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from src.market_regime.classifier import classify_index
from src.market_regime.kiwoom_kr import KiwoomKrCollector, build_index_features
from src.market_regime.models import BreadthFeatures, DataQuality, IndexFeatures, MarketRegime
from src.market_regime.quality import assess_quality
from src.market_regime.service import MarketRegimeService


def index_rows(multiplier: float = 1.0) -> list[dict]:
    return [{"date": f"2025{i // 28 + 1:02d}{i % 28 + 1:02d}", "close": (1000 + i) * multiplier}
            for i in range(260)]


@dataclass
class Bar:
    date: str
    close_price: float


class FakeBroker:
    def __init__(self, breadth_count: int = 60):
        self.index_calls = []
        self.daily_calls = []
        self.breadth_count = breadth_count

    def get_index_daily(self, code, n=90):
        self.index_calls.append((code, n))
        return index_rows(1.0 if code == "0001" else .8)

    def fetch_daily_bars(self, symbol, count=60):
        self.daily_calls.append((symbol, count))
        return [Bar(f"2025{i // 28 + 1:02d}{i % 28 + 1:02d}", 100 + i) for i in range(180, 260)]


class MemoryRepository:
    def __init__(self):
        self.rows = []

    def save(self, value):
        self.rows.insert(0, dict(value))
        return len(self.rows)

    def current(self):
        return self.rows[0] if self.rows else None

    def history(self, limit=30):
        return self.rows[:limit]


class MarketRegimeBackendTests(unittest.TestCase):
    def test_index_features_are_deterministic(self):
        value = build_index_features("0001", index_rows())
        self.assertEqual(value.observations, 260)
        self.assertGreater(value.close, value.sma200)
        self.assertGreater(value.return_20d, 0)

    def test_classifier_has_complete_non_unknown_fallback(self):
        value = IndexFeatures("0001", "20250808", 260, 101, 100, 99, 98, .01, .02, -.01, .2, .2, 1.0)
        self.assertIn(classify_index(value, .5).regime, set(MarketRegime) - {MarketRegime.INSUFFICIENT_DATA})

    def test_quality_levels(self):
        feature = build_index_features("0001", index_rows())
        indices = {"kospi": feature, "kosdaq": feature}
        good = BreadthFeatures(60, 60, feature.session_date, .5, .5, .5)
        degraded = BreadthFeatures(60, 45, feature.session_date, .5, .5, .5)
        insufficient = BreadthFeatures(60, 29, feature.session_date, .5, .5, .5)
        self.assertIs(assess_quality(indices, good).quality, DataQuality.GOOD)
        self.assertIs(assess_quality(indices, degraded).quality, DataQuality.DEGRADED)
        self.assertIs(assess_quality(indices, insufficient).quality, DataQuality.INSUFFICIENT)

    def test_collector_uses_required_kiwoom_contracts(self):
        broker = FakeBroker()
        collector = KiwoomKrCollector(broker, universe=("005930", "000660"))
        indices, breadth = collector.collect()
        self.assertEqual(broker.index_calls, [("0001", 260), ("1001", 260)])
        self.assertEqual(broker.daily_calls, [("005930", 80), ("000660", 80)])
        self.assertEqual(set(indices), {"kospi", "kosdaq"})
        self.assertEqual(breadth.valid_count, 2)

    def test_service_refresh_current_history_and_diagnostics(self):
        broker, repo = FakeBroker(), MemoryRepository()
        collector = KiwoomKrCollector(broker, universe=tuple(f"{i:06d}" for i in range(60)))
        service = MarketRegimeService(
            broker, collector=collector, repository=repo,
            clock=lambda: datetime(2026, 8, 26, 8, 50, tzinfo=ZoneInfo("Asia/Seoul")),
        )
        refreshed = service.refresh("KR")
        self.assertEqual(refreshed["quality"], "good")
        self.assertTrue(refreshed["new_risk_allowed"])
        self.assertEqual(service.current()["session_date"], refreshed["session_date"])
        self.assertEqual(len(service.history(30)), 1)
        self.assertTrue(service.diagnostics()["available"])

    def test_missing_index_becomes_insufficient_not_exception(self):
        broker = FakeBroker()
        original = broker.get_index_daily
        broker.get_index_daily = lambda code, n=90: (_ for _ in ()).throw(RuntimeError("down")) if code == "1001" else original(code, n)
        repo = MemoryRepository()
        collector = KiwoomKrCollector(broker, universe=tuple(f"{i:06d}" for i in range(60)))
        result = MarketRegimeService(broker, collector=collector, repository=repo).refresh()
        self.assertEqual(result["quality"], "insufficient")
        self.assertEqual(result["regime"], "insufficient_data")
        self.assertFalse(result["new_risk_allowed"])

    def test_preflight_cli_exit_code_follows_quality_gate(self):
        from src.market_regime.__main__ import main

        broker = object()
        good_service = Mock()
        good_service.refresh.return_value = {"quality": "degraded"}
        with patch("src.market_regime.__main__.create_domestic_stock_broker", return_value=broker), patch(
            "src.market_regime.__main__.MarketRegimeService", return_value=good_service
        ), redirect_stdout(StringIO()):
            self.assertEqual(main(["preflight", "--market", "KR"]), 0)
        good_service.refresh.assert_called_once_with("KR")

        insufficient_service = Mock()
        insufficient_service.refresh.return_value = {"quality": "insufficient"}
        with patch("src.market_regime.__main__.create_domestic_stock_broker", return_value=broker), patch(
            "src.market_regime.__main__.MarketRegimeService", return_value=insufficient_service
        ), redirect_stdout(StringIO()):
            self.assertEqual(main(["preflight"]), 1)


if __name__ == "__main__":
    unittest.main()
