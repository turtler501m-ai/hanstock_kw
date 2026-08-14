import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.strategy.narrative_collector import build_narrative_entry, collect_narrative_history
from src.strategy.narrative_momentum import load_json_file, save_json_file
from src.strategy.narrative_momentum_runner import run_narrative_momentum_cycle


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <item>
      <title>AI 반도체 투자 확대에 삼성전자 SK하이닉스 강세</title>
      <description>HBM 수요 증가와 데이터센터 투자 확대 기대</description>
      <link>https://example.test/1</link>
    </item>
    <item>
      <title>전력인프라 수요 확대, 변압기 관련주 수혜 기대</title>
      <description>데이터센터 전력망 투자 증가</description>
      <link>https://example.test/2</link>
    </item>
  </channel>
</rss>
"""


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class NarrativeCollectorTest(unittest.TestCase):
    def test_build_narrative_entry_from_articles(self):
        entry = build_narrative_entry(
            [
                {
                    "title": "AI 반도체 투자 확대에 삼성전자 강세",
                    "description": "HBM 수요 증가",
                }
            ],
            ["반도체", "AI"],
            "2026-06-19",
        )

        self.assertEqual(entry["date"], "2026-06-19")
        self.assertGreaterEqual(len(entry["dominant_narratives"]), 1)
        sectors = {sector for item in entry["dominant_narratives"] for sector in item["affected_sectors"]}
        self.assertIn("반도체", sectors)

    def test_collect_narrative_history_saves_today_entry(self):
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "narrative_history.json"
            theme_path = Path(tmp) / "theme_map.json"
            save_json_file(theme_path, {"반도체": [{"ticker": "005930", "name": "삼성전자"}], "AI": []})

            with patch("requests.get", return_value=FakeResponse(RSS_SAMPLE)):
                result = collect_narrative_history(
                    history_path=history_path,
                    theme_map_path=theme_path,
                    today_str="2026-06-19",
                    rss_urls=["https://example.test/rss"],
                )

            saved = load_json_file(history_path, [])
        self.assertTrue(result["generated"])
        self.assertEqual(saved[0]["date"], "2026-06-19")
        self.assertGreaterEqual(len(saved[0]["dominant_narratives"]), 1)

    def test_runner_auto_collects_before_scan(self):
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "narrative_history.json"
            theme_path = Path(tmp) / "theme_map.json"
            latest_path = Path(tmp) / "latest.json"
            save_json_file(theme_path, {"반도체": [{"ticker": "005930", "name": "삼성전자"}], "AI": []})

            with patch("requests.get", return_value=FakeResponse(RSS_SAMPLE)):
                result = run_narrative_momentum_cycle(
                    save_candidates=False,
                    auto_collect=True,
                    history_path=history_path,
                    theme_map_path=theme_path,
                    latest_path=latest_path,
                )

        self.assertEqual(result["status"]["state"], "fresh")
        self.assertTrue(result["collection"]["generated"])
        self.assertGreaterEqual(result["total_scanned"], 1)


if __name__ == "__main__":
    unittest.main()
