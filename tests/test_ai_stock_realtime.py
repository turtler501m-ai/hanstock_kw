# -*- coding: utf-8 -*-
"""2차 실시간 타이밍·자동 승인 큐 테스트 (§4.8·§5.12)."""
import unittest

from src.db.repository import init_db, connect_db
from src.db import ai_stock_repository as repo
from src.ai_stock import market_data, realtime_service, watchlist_service, automation_service
from src.ai_stock.constants import DATA_GOOD, WATCH_WATCHING, WATCH_CONFIRMED

_AI_TABLES = [
    "ai_stock_timing_signals", "ai_stock_execution_runs", "ai_stock_execution_plans",
    "ai_stock_performance", "ai_stock_watch_events", "ai_stock_watchlist",
    "ai_stock_candidates", "ai_stock_automation_policies", "ai_stock_scans",
]


class _Provider:
    def __init__(self, price=None):
        self.price = price

    def universe_items(self, market):
        return []

    def quote(self, market, symbol):
        from src.ai_stock.freshness import now

        return {"price": self.price, "data_as_of": now().isoformat()} if self.price is not None else None

    def daily_series(self, market, symbol):
        return None

    def index_series(self, market):
        return {}


class _FailingQuoteProvider(_Provider):
    def quote(self, market, symbol):
        raise RuntimeError("quote unavailable")


class RealtimeTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with connect_db() as conn:
            for t in _AI_TABLES:
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        self._orig = market_data.get_provider()

    def tearDown(self):
        market_data.set_provider(self._orig)

    def _confirmed_candidate(self):
        from src.ai_stock.freshness import now
        cid = repo.save_candidate({
            "scan_id": 1, "market": "KR", "symbol": "005930", "name": "삼성",
            "strategy_id": "ai_stock_default_v1", "current_price": 70000,
            "rule_score": 70, "technical_score": 75, "momentum_score": 70,
            "narrative_score": 60, "ai_score": 0, "risk_score": 20,
            "final_score": 80, "decision": "strong_watch", "data_quality": DATA_GOOD,
            "data_as_of": now().isoformat(),
        })
        watchlist_service.register(repo.get_candidate(cid))
        watchlist_service.transition(cid, WATCH_WATCHING)
        watchlist_service.transition(cid, WATCH_CONFIRMED)
        return cid

    def test_signal_only_for_pool(self):
        market_data.set_provider(_Provider(price=72000))
        # 후보 풀에 없는 candidate_id로 신호 생성 시도 → 거부(§4.8)
        with self.assertRaises(ValueError):
            realtime_service.evaluate_timing("KR", 99999, realtime_quote={"price": 100})

    def test_breakout_entry_signal(self):
        from src.ai_stock.freshness import now

        cid = self._confirmed_candidate()
        market_data.set_provider(_Provider(price=72000))  # 진입가 70000 대비 돌파
        sig = realtime_service.evaluate_timing("KR", cid, realtime_quote={"price": 72000, "data_as_of": now().isoformat()})
        self.assertEqual(sig["signal_type"], "entry")
        self.assertEqual(sig["decision"], "proceed")

    def test_missing_quote_timestamp_blocks_entry(self):
        cid = self._confirmed_candidate()
        sig = realtime_service.evaluate_timing("KR", cid, realtime_quote={"price": 72000})
        self.assertNotEqual(sig["signal_type"], "entry")
        self.assertIn("stale", sig["blocked_reason"])

    def test_old_quote_timestamp_blocks_entry(self):
        from datetime import timedelta
        from src.ai_stock.freshness import now

        cid = self._confirmed_candidate()
        old = (now() - timedelta(minutes=30)).isoformat()
        sig = realtime_service.evaluate_timing("KR", cid, realtime_quote={"price": 72000, "data_as_of": old})
        self.assertNotEqual(sig["signal_type"], "entry")
        self.assertIn("stale", sig["blocked_reason"])

    def test_disconnected_blocks_entry(self):
        cid = self._confirmed_candidate()
        market_data.set_provider(_Provider(price=None))
        sig = realtime_service.evaluate_timing("KR", cid, realtime_quote=None)
        self.assertNotEqual(sig["signal_type"], "entry")  # 단절 시 신규 진입 없음

    def test_realtime_cycle_runs_over_pool(self):
        self._confirmed_candidate()
        market_data.set_provider(_Provider(price=72000))
        result = realtime_service.run_realtime_cycle("KR")
        self.assertEqual(result["pool_size"], 1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["errors"], [])

    def test_realtime_cycle_isolates_quote_failure(self):
        self._confirmed_candidate()
        market_data.set_provider(_FailingQuoteProvider())
        result = realtime_service.run_realtime_cycle("KR")
        self.assertEqual(result["pool_size"], 1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("stale", result["signals"][0]["blocked_reason"])


class ApprovalQueueTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with connect_db() as conn:
            for t in _AI_TABLES:
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM approvals WHERE source='ai_stock'")
            conn.commit()

    def test_queue_approval_creates_pending(self):
        cand = {"symbol": "005930", "name": "삼성", "final_score": 85, "decision": "strong_watch"}
        plan = {"quantity": 3, "entry_price": 70000}
        aid = automation_service._queue_approval("KR", cand, plan, "ai_stock_default_v1")
        self.assertGreater(aid, 0)
        with connect_db() as conn:
            row = conn.execute("SELECT status, source FROM approvals WHERE id=?", (aid,)).fetchone()
            self.assertEqual(row[0], "pending")
            self.assertEqual(row[1], "ai_stock")
        with connect_db() as conn:
            conn.execute("DELETE FROM approvals WHERE id=?", (aid,))
            conn.commit()

    def test_us_approval_uses_mistock_queue(self):
        from src.mistock import db as mistock_db

        cand = {"symbol": "AAPL", "name": "Apple", "final_score": 85, "decision": "watch"}
        plan = {"quantity": 1, "entry_price": 140}
        aid = automation_service._queue_approval("US", cand, plan, "ai_stock_default_v1")
        self.assertGreater(aid, 0)
        conn = mistock_db.connect_db()
        try:
            row = conn.execute("SELECT status, source FROM approvals WHERE id=?", (aid,)).fetchone()
            self.assertEqual(row[0], "pending")
            self.assertEqual(row[1], "ai_stock")
            conn.execute("DELETE FROM approvals WHERE id=?", (aid,))
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
