import unittest
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException

from src import trader
from src.dashboard import app
from src.dashboard import core as dashboard_core
from src.dashboard.routes import narrative_momentum
from src.strategy.narrative_momentum import save_json_file


class NarrativeMomentumDashboardTest(unittest.TestCase):
    def test_routes_are_registered(self):
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/narrative-momentum", paths)
        self.assertIn("/api/narrative-momentum/status", paths)
        self.assertIn("/api/narrative-momentum/latest", paths)
        self.assertIn("/api/narrative-momentum/scan", paths)
        self.assertIn("/api/narrative-momentum/history", paths)
        self.assertIn("/api/narrative-momentum/collect", paths)
        self.assertIn("/api/narrative-momentum/theme-map", paths)
        self.assertIn("/api/narrative-momentum/schedule", paths)
        self.assertIn("/api/narrative-momentum/run-scheduled", paths)
        self.assertIn("/api/narrative-momentum/schedule-history", paths)

    def test_template_links_dedicated_assets(self):
        with open("web/templates/narrative_momentum.html", encoding="utf-8") as handle:
            template = handle.read()
        with open("web/static/js/narrative_momentum.js", encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("/static/css/narrative_momentum.css", template)
        self.assertIn("/static/js/narrative_momentum.js", template)
        self.assertIn("narrative_momentum.css?v=6", template)
        self.assertIn("narrative_momentum.js?v=6", template)
        self.assertIn("narrative-readiness", template)
        self.assertIn("narrative-action-status", template)
        self.assertIn("btn-narrative-oneclick", template)
        self.assertIn("narrative-tab-results", template)
        self.assertIn("narrative-results-body", template)
        self.assertIn("narrative-result-detail", template)
        self.assertIn("btn-narrative-collect", template)
        self.assertIn("narrative-history-editor", template)
        self.assertIn("가격/수량", template)
        self.assertIn("/api/narrative-momentum", script)
        self.assertIn("/api/narrative-momentum/history", script)
        self.assertIn("/api/narrative-momentum/collect", script)
        self.assertIn("/api/narrative-momentum/schedule", script)
        self.assertIn("/api/narrative-momentum/run-scheduled", script)
        self.assertIn("auto_collect", script)
        self.assertIn("뉴스 자동생성 완료", script)
        self.assertIn("자동 실행 완료", script)
        self.assertIn("후보저장 완료", script)
        self.assertIn("setActionStatus", script)
        self.assertIn("renderResultDetail", script)
        self.assertIn("groupResultsByDate", script)
        self.assertIn("세부목록", template)
        self.assertIn("narrative-schedule-history-body", template)
        self.assertIn("narrative-schedule-summary", template)
        self.assertIn("내러티브 이력 파일이 없어", script)
        self.assertIn("입력 내러티브 이력이 없습니다.", script)
        self.assertIn("테마맵", script)
        self.assertIn("narrative-price", script)
        self.assertIn("지정가와 수량", script)

    def test_theme_map_uses_readable_korean_labels(self):
        with open("config/theme_map.json", encoding="utf-8") as handle:
            theme_map = __import__("json").load(handle)

        self.assertIn("반도체", theme_map)
        self.assertIn("AI", theme_map)
        self.assertIn({"ticker": "005930", "name": "삼성전자"}, theme_map["반도체"])
        serialized = __import__("json").dumps(theme_map, ensure_ascii=False)
        self.assertNotIn("諛섎", serialized)
        self.assertNotIn("?쇱", serialized)

    def test_save_history_writes_utf8_json(self):
        payload = {
            "history": [
                {
                    "date": "2026-06-18",
                    "dominant_narratives": [
                        {
                            "theme": "AI 반도체 투자 확대",
                            "strength": 88,
                            "sentiment": "bullish",
                            "direction": "rising",
                            "affected_sectors": ["반도체"],
                        }
                    ],
                }
            ]
        }
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "narrative_history.json"
            with patch.object(narrative_momentum, "NARRATIVE_HISTORY_PATH", path):
                result = narrative_momentum.save_narrative_momentum_history(payload)
            self.assertTrue(result["ok"])
            text = path.read_text(encoding="utf-8")
        self.assertIn("AI 반도체 투자 확대", text)

    def test_queue_approval_rejects_ticker_not_in_current_signals(self):
        today = trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
        history = [
            {
                "date": today,
                "dominant_narratives": [
                    {
                        "theme": "AI 반도체 투자 확대",
                        "strength": 88,
                        "sentiment": "bullish",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    }
                ],
            }
        ]
        theme_map = {"반도체": [{"ticker": "005930", "name": "삼성전자"}]}
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "narrative_history.json"
            theme_path = Path(tmp) / "theme_map.json"
            save_json_file(history_path, history)
            save_json_file(theme_path, theme_map)
            with patch.object(narrative_momentum, "NARRATIVE_HISTORY_PATH", history_path), \
                    patch.object(narrative_momentum, "THEME_MAP_PATH", theme_path), \
                    patch.object(narrative_momentum, "_has_pending_buy", return_value=False), \
                    patch.object(narrative_momentum, "_create_approval_row", return_value=10):
                with self.assertRaises(HTTPException) as ctx:
                    narrative_momentum.queue_narrative_approval(
                        {"ticker": "000660", "name": "SK하이닉스", "score": 99, "qty": 1, "price": 100000}
                    )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_queue_approval_accepts_current_signal_with_manual_price(self):
        today = trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
        history = [
            {
                "date": today,
                "dominant_narratives": [
                    {
                        "theme": "AI 반도체 투자 확대",
                        "strength": 88,
                        "sentiment": "bullish",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    }
                ],
            }
        ]
        theme_map = {"반도체": [{"ticker": "005930", "name": "삼성전자"}]}
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "narrative_history.json"
            theme_path = Path(tmp) / "theme_map.json"
            save_json_file(history_path, history)
            save_json_file(theme_path, theme_map)
            with patch.object(narrative_momentum, "NARRATIVE_HISTORY_PATH", history_path), \
                    patch.object(narrative_momentum, "THEME_MAP_PATH", theme_path), \
                    patch.object(narrative_momentum, "_has_pending_buy", return_value=False), \
                    patch.object(narrative_momentum, "_create_approval_row", return_value=10) as create:
                result = narrative_momentum.queue_narrative_approval(
                    {"ticker": "005930", "name": "임의명", "score": 0, "qty": 2, "price": 80000, "reason": "client score 0"}
                )
        self.assertEqual(result["id"], 10)
        self.assertEqual(create.call_args.args[0]["name"], "삼성전자")
        self.assertEqual(create.call_args.args[0]["qty"], 2)
        self.assertEqual(create.call_args.args[0]["price"], 80000)
        self.assertIn("내러티브 모멘텀", create.call_args.args[0]["reason"])
        self.assertIn("점", create.call_args.args[0]["reason"])
        self.assertIn("요청메모: client score 0", create.call_args.args[0]["reason"])

    def test_queue_approval_rejects_missing_or_zero_qty(self):
        today = trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
        history = [
            {
                "date": today,
                "dominant_narratives": [
                    {
                        "theme": "AI 반도체 투자 확대",
                        "strength": 88,
                        "sentiment": "bullish",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    }
                ],
            }
        ]
        theme_map = {"반도체": [{"ticker": "005930", "name": "삼성전자"}]}
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "narrative_history.json"
            theme_path = Path(tmp) / "theme_map.json"
            save_json_file(history_path, history)
            save_json_file(theme_path, theme_map)
            with patch.object(narrative_momentum, "NARRATIVE_HISTORY_PATH", history_path), \
                    patch.object(narrative_momentum, "THEME_MAP_PATH", theme_path), \
                    patch.object(narrative_momentum, "_has_pending_buy", return_value=False), \
                    patch.object(narrative_momentum, "_create_approval_row", return_value=10):
                for payload in (
                    {"ticker": "005930", "price": 80000},
                    {"ticker": "005930", "qty": 0, "price": 80000},
                ):
                    with self.subTest(payload=payload):
                        with self.assertRaises(HTTPException) as ctx:
                            narrative_momentum.queue_narrative_approval(payload)
                        self.assertEqual(ctx.exception.status_code, 400)
                        self.assertIn("qty", ctx.exception.detail)

    def test_save_candidates_persists_zero_price_analysis_signals(self):
        with patch.object(narrative_momentum.runner, "save_scanned_candidate", return_value=1) as save:
            saved = narrative_momentum._save_candidates(
                [{"ticker": "005930", "name": "삼성전자", "score": 90, "current_price": 0, "reasons": []}]
            )
        self.assertEqual(saved, 1)
        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["price"], 0)
        self.assertEqual(save.call_args.kwargs["scoring"]["top_features"]["price_source"], "not_available")

    def test_scan_persists_saved_count_after_candidate_save(self):
        today = trader.datetime.now(trader.KST).strftime("%Y-%m-%d")
        history = [
            {
                "date": today,
                "dominant_narratives": [
                    {
                        "theme": "AI 반도체 투자 확대",
                        "strength": 88,
                        "sentiment": "bullish",
                        "direction": "rising",
                        "affected_sectors": ["반도체"],
                    }
                ],
            }
        ]
        theme_map = {"반도체": [{"ticker": "005930", "name": "삼성전자"}]}
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "narrative_history.json"
            theme_path = Path(tmp) / "theme_map.json"
            latest_path = Path(tmp) / "latest.json"
            save_json_file(history_path, history)
            save_json_file(theme_path, theme_map)
            with patch.object(narrative_momentum, "NARRATIVE_HISTORY_PATH", history_path), \
                    patch.object(narrative_momentum, "THEME_MAP_PATH", theme_path), \
                    patch.object(narrative_momentum, "LATEST_RESULT_PATH", latest_path), \
                    patch.object(narrative_momentum.runner, "save_candidates_from_signals", return_value=1):
                result = narrative_momentum.scan_narrative_momentum({"save_candidates": True})
            saved_payload = narrative_momentum.load_json_file(latest_path, {})
        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(saved_payload["saved_count"], 1)

    def test_run_scheduled_persists_scheduler_summary(self):
        with patch.object(narrative_momentum.runner, "run_narrative_momentum_cycle", return_value={
            "strategy_id": "narrative_momentum_strategy",
            "total_scanned": 2,
            "saved_count": 1,
            "summary": {"candidate_count": 2, "saved_count": 1},
            "errors": [],
        }) as run_mock, patch.object(narrative_momentum, "save_scheduler_result") as save_mock:
            result = narrative_momentum.run_narrative_momentum_scheduled({"save_candidates": True})

        self.assertEqual(result["summary"]["candidate_count"], 2)
        run_mock.assert_called_once()
        self.assertTrue(run_mock.call_args.kwargs["save_candidates"])
        save_mock.assert_called_once()
        self.assertEqual(save_mock.call_args.args[0], "execute")

    def test_auto_approval_pending_ids_exclude_narrative_source_when_requested(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "approvals.sqlite"

            class ClosingConnection(sqlite3.Connection):
                def __exit__(self, exc_type, exc_value, traceback):
                    super().__exit__(exc_type, exc_value, traceback)
                    self.close()

            def connect_db():
                return sqlite3.connect(db_path, factory=ClosingConnection)

            with patch.object(dashboard_core.trader, "connect_db", side_effect=connect_db):
                with connect_db() as conn:
                    conn.execute(
                        """
                        CREATE TABLE approvals (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            name TEXT NOT NULL,
                            action TEXT NOT NULL,
                            qty INTEGER NOT NULL,
                            price INTEGER NOT NULL,
                            reason TEXT,
                            source TEXT,
                            status TEXT NOT NULL,
                            response_msg TEXT
                        )
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO approvals
                        (created_at, updated_at, symbol, name, action, qty, price, reason, source, status, response_msg)
                        VALUES
                        ('2026-06-19', '2026-06-19', '005930', '삼성전자', 'buy', 1, 80000, '', 'narrative_momentum', 'pending', ''),
                        ('2026-06-19', '2026-06-19', '000660', 'SK하이닉스', 'buy', 1, 180000, '', 'dashboard', 'pending', '')
                        """
                    )
                with patch.object(dashboard_core, "_init_approval_db", return_value=None):
                    ids = dashboard_core._pending_approval_ids(
                        exclude_sources=dashboard_core.AUTO_APPROVAL_EXCLUDED_SOURCES
                    )
                with connect_db() as conn:
                    symbols = [
                        row[0]
                        for row in conn.execute(
                            f"SELECT symbol FROM approvals WHERE id IN ({', '.join('?' for _ in ids)}) ORDER BY id",
                            ids,
                        ).fetchall()
                    ]
        self.assertEqual(symbols, ["000660"])


if __name__ == "__main__":
    unittest.main()
