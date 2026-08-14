import unittest
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

from src.db.repository import (
    connect_db,
    init_db,
    save_scanned_candidate,
    get_scanned_candidates_history,
    get_ai_strategy_performance,
    refresh_scanned_candidate_forward_returns,
    review_ai_strategy_performance,
    save_ai_strategies,
    load_ai_strategies,
    delete_scanned_candidate,
    save_trade,
    KST
)

class ScannedCandidatesPersistenceTests(unittest.TestCase):
    def setUp(self):
        # Use an in-memory SQLite database for testing isolation
        self.patch_db = patch("src.db.repository.connect_db")
        self.mock_connect = self.patch_db.start()
        
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA journal_mode=MEMORY")
        
        # Wrap self.conn inside DBWrapper-like behavior
        from src.db.repository import DBWrapper
        self.mock_connect.return_value = DBWrapper(self.conn, is_pg=False)
        
        # Initialize database tables
        init_db()

    def tearDown(self):
        self.conn.close()
        self.patch_db.stop()

    def test_init_db_creates_scanned_candidates_table(self):
        cursor = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scanned_candidates'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "scanned_candidates")

    def test_save_and_retrieve_scanned_candidate(self):
        # Save a sample candidate
        save_scanned_candidate(
            symbol="005930",
            name="삼성전자",
            score=3,
            reasons=["rsi_oversold", "sma_support"],
            price=72000,
            env="demo",
            indicators={"rsi": 28.5, "rsi2": 29.1, "macd_hist": -1.2, "sma20": 71500, "sma60": 73000}
        )

        history = get_scanned_candidates_history(limit=10, days=1)
        self.assertEqual(len(history), 1)
        
        cand = history[0]
        self.assertEqual(cand["symbol"], "005930")
        self.assertEqual(cand["name"], "삼성전자")
        self.assertEqual(cand["score"], 3)
        self.assertEqual(cand["reasons"], "rsi_oversold,sma_support")
        self.assertEqual(cand["price"], 72000)
        self.assertEqual(cand["env"], "demo")
        self.assertEqual(cand["rsi"], 28.5)
        self.assertEqual(cand["rsi2"], 29.1)
        self.assertEqual(cand["macd_hist"], -1.2)
        self.assertEqual(cand["sma20"], 71500)
        self.assertEqual(cand["sma60"], 73000)

    def test_save_scanned_candidate_persists_strategy_metadata(self):
        save_scanned_candidate(
            symbol="035420",
            name="NAVER",
            score=4,
            reasons=["ai_ranked"],
            price=180000,
            env="demo",
            indicators={"rsi": 35.0},
            strategy={
                "id": "gpt_rebound_balanced_v1",
                "strategy_version": 3,
                "profile_hash": "hash123",
            },
            ranker_model="gpt-5-mini",
            optimizer="score_tilted_inverse_vol",
            scoring={
                "rule_score": 2.5,
                "ml_score": 0.8,
                "final_score": 3.4,
                "ai_model_status": "ready",
                "ai_fallback_reason": None,
                "top_features": [{"name": "rsi2", "value": 12.0}],
            },
        )

        history = get_scanned_candidates_history(limit=10, days=1)
        cand = history[0]
        self.assertEqual(cand["strategy_id"], "gpt_rebound_balanced_v1")
        self.assertEqual(cand["strategy_version"], 3)
        self.assertEqual(cand["profile_hash"], "hash123")
        self.assertEqual(cand["ranker_model"], "gpt-5-mini")
        self.assertEqual(cand["optimizer"], "score_tilted_inverse_vol")
        self.assertEqual(cand["rule_score"], 2.5)
        self.assertEqual(cand["ml_score"], 0.8)
        self.assertEqual(cand["final_score"], 3.4)
        self.assertEqual(cand["ai_model_status"], "ready")
        self.assertIn("rsi2", cand["top_features_json"])

    def test_ai_strategy_performance_summarizes_candidate_history(self):
        save_scanned_candidate(
            symbol="035420",
            name="NAVER",
            score=4,
            reasons=["ai_ranked"],
            price=180000,
            env="demo",
            strategy={"id": "perf_strategy", "strategy_version": 1, "profile_hash": "hash"},
            ranker_model="gpt-5-mini",
            optimizer="score_tilted_inverse_vol",
            scoring={
                "rule_score": 2.0,
                "ml_score": 0.9,
                "final_score": 3.45,
                "ai_model_status": "ready",
            },
        )
        save_scanned_candidate(
            symbol="000660",
            name="SK하이닉스",
            score=3,
            reasons=["fallback"],
            price=120000,
            env="demo",
            strategy={"id": "perf_strategy", "strategy_version": 1, "profile_hash": "hash"},
            ranker_model="gpt-5-mini",
            optimizer="score_tilted_inverse_vol",
            scoring={
                "rule_score": 3.0,
                "final_score": 3.0,
                "ai_model_status": "fallback",
            },
        )

        summary = get_ai_strategy_performance("perf_strategy", days=1)

        self.assertEqual(summary["candidate_count"], 2)
        self.assertEqual(summary["avg_final_score"], 3.225)
        self.assertEqual(summary["ai_model_status_counts"]["ready"], 1)
        self.assertEqual(summary["ai_model_status_counts"]["fallback"], 1)
        self.assertEqual(summary["optimizer_counts"]["score_tilted_inverse_vol"], 2)

    def test_forward_returns_are_calculated_from_daily_chart_cache(self):
        today = datetime.now(KST).date()
        dates = {
            0: today.strftime("%Y-%m-%d"),
            1: (today + timedelta(days=1)).strftime("%Y-%m-%d"),
            5: (today + timedelta(days=5)).strftime("%Y-%m-%d"),
            20: (today + timedelta(days=20)).strftime("%Y-%m-%d"),
        }
        for offset, close in [(0, 100.0), (1, 103.0), (5, 110.0), (20, 95.0)]:
            self.conn.execute(
                """
                INSERT INTO daily_charts (symbol, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("005930", dates[offset], close, close, close, close, 1000),
            )

        save_scanned_candidate(
            symbol="005930",
            name="Samsung",
            score=4,
            reasons=["forward_return"],
            price=100,
            env="demo",
            strategy={"id": "return_strategy", "strategy_version": 1, "profile_hash": "hash"},
            scoring={"final_score": 4.0, "ai_model_status": "ready"},
        )

        result = refresh_scanned_candidate_forward_returns()
        summary = get_ai_strategy_performance("return_strategy", days=1)

        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(summary["avg_return_1d"], 3.0)
        self.assertEqual(summary["avg_return_5d"], 10.0)
        self.assertEqual(summary["avg_return_20d"], -5.0)
        self.assertEqual(summary["win_rate_5d"], 100.0)

    def test_ai_strategy_performance_review_marks_weak_strategy_for_review(self):
        save_ai_strategies([
            {
                "id": "weak_strategy",
                "name": "Weak Strategy",
                "provider": "none",
                "model": "none",
                "weight": 0.0,
                "description": "Weak test strategy",
                "selected": True,
                "status": "approved",
            }
        ])
        for idx in range(5):
            save_scanned_candidate(
                symbol=f"10000{idx}",
                name=f"Weak {idx}",
                score=1,
                reasons=["weak"],
                price=10000,
                env="demo",
                strategy={"id": "weak_strategy", "strategy_version": 1, "profile_hash": "hash"},
                optimizer="score_tilted_inverse_vol",
                scoring={
                    "rule_score": 1.0,
                    "final_score": 1.8,
                    "ai_model_status": "fallback",
                },
            )

        result = review_ai_strategy_performance("weak_strategy", days=1)
        loaded = load_ai_strategies()
        strategy = next(item for item in loaded if item["id"] == "weak_strategy")

        self.assertTrue(result["changed"])
        self.assertEqual(result["new_status"], "review_required")
        self.assertEqual(strategy["status"], "review_required")
        self.assertIn("low average final score", result["warnings"])
        self.assertIn("high AI fallback rate", result["warnings"])

    def test_ai_strategy_performance_counts_approvals_and_trades(self):
        now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """
            INSERT INTO approvals (
                created_at, updated_at, symbol, name, action, qty, price, reason, source,
                status, response_msg, strategy_id, strategy_version, profile_hash, source_candidate_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                "005930",
                "Samsung",
                "buy",
                1,
                70000,
                "candidate",
                "dashboard",
                "executed",
                "ok",
                "trade_strategy",
                3,
                "hash-trade",
                11,
            ),
        )
        save_trade(
            "005930",
            "Samsung",
            "buy",
            1,
            70000,
            "candidate",
            True,
            False,
            order_status="simulated",
            strategy_id="trade_strategy",
            strategy_version=3,
            profile_hash="hash-trade",
            source_approval_id=1,
        )

        summary = get_ai_strategy_performance("trade_strategy", days=1)
        trades = summary["trade_summary"]

        self.assertEqual(trades["approval_count"], 1)
        self.assertEqual(trades["order_count"], 1)
        self.assertEqual(trades["filled_count"], 1)
        self.assertEqual(trades["fill_rate"], 100.0)
        self.assertEqual(trades["approval_status_counts"]["executed"], 1)

    def test_delete_scanned_candidate(self):
        # Save sample candidate
        save_scanned_candidate(
            symbol="000660",
            name="SK하이닉스",
            score=2,
            reasons="macd_golden_cross",
            price=120000,
            env="demo"
        )
        
        history = get_scanned_candidates_history()
        self.assertEqual(len(history), 1)
        cand_id = history[0]["id"]
        
        # Delete candidate
        deleted_count = delete_scanned_candidate(cand_id)
        self.assertEqual(deleted_count, 1)
        
        # Confirm deleted
        history_after = get_scanned_candidates_history()
        self.assertEqual(len(history_after), 0)


if __name__ == "__main__":
    unittest.main()
