import importlib.util
from pathlib import Path
import tempfile
import unittest


def load_rehearsal_module():
    path = Path(__file__).resolve().parent.parent / "tools" / "demo-trading-rehearsal.py"
    spec = importlib.util.spec_from_file_location("demo_trading_rehearsal", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DemoTradingRehearsalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_rehearsal_module()

    def test_demo_order_plan_with_explicit_price_does_not_touch_kis_without_confirm(self):
        original_readiness = self.module.dashboard.get_demo_trading_readiness
        original_get_api = self.module.dashboard._get_api
        try:
            self.module.dashboard.get_demo_trading_readiness = lambda: {"ready": True}
            self.module.dashboard._get_api = lambda: self.fail("KIS API should not be constructed")

            result = self.module._demo_order_rehearsal(
                symbol="005930",
                side="buy",
                qty=1,
                price=1000,
                confirm=False,
                sync_days=7,
            )

            self.assertTrue(result["ok"])
            self.assertFalse(result["submitted"])
            self.assertEqual(result["plan"]["limit_price"], 1000)
        finally:
            self.module.dashboard.get_demo_trading_readiness = original_readiness
            self.module.dashboard._get_api = original_get_api

    def test_demo_order_plan_without_price_skips_quote_without_kis_lookup(self):
        original_readiness = self.module.dashboard.get_demo_trading_readiness
        original_get_api = self.module.dashboard._get_api
        try:
            self.module.dashboard.get_demo_trading_readiness = lambda: {"ready": True}
            self.module.dashboard._get_api = lambda: self.fail("KIS API should not be constructed")

            result = self.module._demo_order_rehearsal(
                symbol="005930",
                side="buy",
                qty=1,
                price=0,
                confirm=False,
                sync_days=7,
            )

            self.assertTrue(result["ok"])
            self.assertFalse(result["submitted"])
            self.assertEqual(result["plan"]["limit_price"], 0)
        finally:
            self.module.dashboard.get_demo_trading_readiness = original_readiness
            self.module.dashboard._get_api = original_get_api

    def test_confirmed_demo_order_saves_trade_and_syncs_status(self):
        original_readiness = self.module.dashboard.get_demo_trading_readiness
        original_get_api = self.module.dashboard._get_api
        original_save_trade = self.module.trader.save_trade
        original_sync = self.module.dashboard._sync_order_status_from_history

        class FakeApi:
            def place_order(self, symbol, side, price, qty):
                return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "D12345"}}

        saved = []
        try:
            self.module.dashboard.get_demo_trading_readiness = lambda: {"ready": True}
            self.module.dashboard._get_api = lambda: FakeApi()
            self.module.trader.save_trade = lambda *args, **kwargs: saved.append((args, kwargs))
            self.module.dashboard._sync_order_status_from_history = lambda api, days=7: {"ok": True, "updated_count": 1}

            result = self.module._demo_order_rehearsal(
                symbol="005930",
                side="buy",
                qty=1,
                price=1000,
                confirm=True,
                sync_days=7,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["submitted"])
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0][1]["order_status"], "submitted")
            self.assertEqual(result["order_status_sync"]["updated_count"], 1)
        finally:
            self.module.dashboard.get_demo_trading_readiness = original_readiness
            self.module.dashboard._get_api = original_get_api
            self.module.trader.save_trade = original_save_trade
            self.module.dashboard._sync_order_status_from_history = original_sync

    def test_write_report_creates_runtime_json_evidence(self):
        report = {"ok": True, "mode": "kis_demo_auto_rehearsal"}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            path = Path(tmpdir) / ".runtime" / "rehearsal.json"
            written = self.module.write_report(report, path)

            self.assertEqual(written, path)
            self.assertIn('"ok": true', path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
