import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class KiwoomDashboardBrandingTests(unittest.TestCase):
    def test_core_pages_have_kiwoom_identity(self):
        expected = {
            "web/templates/index.html": "키움 한스톡",
            "web/templates/mistock/index.html": "키움 미스톡",
            "web/templates/env_settings.html": "키움 한스톡",
        }
        for relative_path, title in expected.items():
            with self.subTest(page=relative_path):
                content = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(title, content)
                self.assertIn("KIWOOM REST API", content)
                self.assertIn("KIWOOM · PORT 8001", content)
                self.assertIn("kiwoom-dashboard", content)

    def test_kiwoom_theme_uses_warm_palette(self):
        content = (ROOT / "web/static/css/style.css").read_text(encoding="utf-8")
        self.assertIn("body.kiwoom-dashboard", content)
        self.assertIn("#f97316", content)
        self.assertIn(".kiwoom-service-badge", content)


if __name__ == "__main__":
    unittest.main()
