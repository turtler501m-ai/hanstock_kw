import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.db.custom_strategy_registry import sync_custom_rules_to_db
from src.db.connection import ClosingConnection


class CustomStrategyRegistryTests(unittest.TestCase):
    def test_sync_preserves_dashboard_owned_profile_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_dir = Path(temp_dir)
            (rules_dir / "sample_strategy.py").write_text(
                'STRATEGY_PROFILE = {"risk": {"max_daily_ai_orders": 3}}\n'
                'class SampleStrategy:\n'
                '    """샘플 전략\n\n    코드 설명"""\n',
                encoding="utf-8",
            )
            conn = sqlite3.connect(":memory:", factory=ClosingConnection)
            self.addCleanup(conn.close)
            conn.execute(
                """
                CREATE TABLE ai_strategies (
                    id TEXT PRIMARY KEY, name TEXT, provider TEXT, model TEXT,
                    weight REAL, description TEXT, selected INTEGER, status TEXT,
                    profile_json TEXT, strategy_version INTEGER, profile_hash TEXT
                )
                """
            )
            with patch("src.db.custom_strategy_registry.CUSTOM_RULES_DIR", rules_dir):
                sync_custom_rules_to_db(conn)
                row = conn.execute(
                    "SELECT profile_json FROM ai_strategies WHERE id='sample_strategy'"
                ).fetchone()
                profile = json.loads(row[0])
                profile["risk"]["max_daily_ai_orders"] = 30
                conn.execute(
                    "UPDATE ai_strategies SET name=?, description=?, profile_json=? WHERE id=?",
                    ("사용자 이름", "사용자 설명", json.dumps(profile), "sample_strategy"),
                )
                sync_custom_rules_to_db(conn)

            row = conn.execute(
                "SELECT name, description, profile_json FROM ai_strategies WHERE id='sample_strategy'"
            ).fetchone()
            self.assertEqual(row[0], "사용자 이름")
            self.assertEqual(row[1], "사용자 설명")
            self.assertEqual(json.loads(row[2])["risk"]["max_daily_ai_orders"], 30)


if __name__ == "__main__":
    unittest.main()
