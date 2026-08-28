import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.dashboard.routes import mistock
from src.mistock import db as mistock_db
from src.mistock import trader
from src.mistock import scheduler
from src.mistock.config import config


class _Broker:
    def __init__(self, response=None, error=None):
        self.response = response or {"rt_cd": "0", "msg1": "order accepted", "output": {"ODNO": "US-100"}}
        self.error = error

    def place_overseas_order(self, *_args):
        if self.error:
            raise self.error
        return self.response

    def cancel_overseas_order(self, *_args, **_kwargs):
        return {"rt_cd": "0", "msg1": "cancel accepted"}

    def revise_overseas_order(self, *_args, **_kwargs):
        return {"rt_cd": "0", "msg1": "revision accepted"}


class MistockManagedOrdersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original = {
            "trade_db_path": config.trade_db_path,
            "trading_env": config.trading_env,
            "dry_run": config.dry_run,
            "enable_live_trading": config.enable_live_trading,
            "total_capital": config.total_capital,
        }
        object.__setattr__(config, "trade_db_path", Path(self.temp_dir.name) / "managed.sqlite")
        object.__setattr__(config, "total_capital", 10_000.0)
        mistock_db.init_db()

    def tearDown(self) -> None:
        for key, value in self.original.items():
            object.__setattr__(config, key, value)
        self.temp_dir.cleanup()

    def _place(self, broker, **kwargs):
        with patch("src.online_access.require_online_access"), patch.object(
            trader, "_get_broker_client", return_value=broker
        ), patch.object(trader, "notify_slack_order"), patch.object(
            trader, "get_usd_krw_rate", return_value=1380.0
        ):
            return trader.place_order("AAPL", "buy", 2, 100, "test", **kwargs)

    def test_managed_order_schema_and_client_key_are_idempotent(self):
        data = {
            "client_order_key": "order-once", "symbol": "AAPL", "action": "buy",
            "requested_qty": 2, "requested_price": 100, "status": "created",
        }
        first = mistock_db.create_managed_order(data)
        second = mistock_db.create_managed_order(data)

        self.assertEqual(first, second)
        rows = mistock_db.rows("SELECT * FROM managed_orders WHERE client_order_key=?", ("order-once",))
        self.assertEqual(len(rows), 1)
        self.assertTrue({
            "broker_order_no", "approval_id", "strategy_id", "requested_qty",
            "requested_price", "filled_qty", "avg_fill_price", "status",
            "last_error", "broker_payload", "created_at", "updated_at",
        }.issubset(rows[0]))

    def test_scheduler_excludes_symbols_rejected_by_kiwoom_as_unknown(self):
        order_id = mistock_db.create_managed_order({
            "client_order_key": "unsupported-avb",
            "symbol": "AVB",
            "action": "buy",
            "requested_qty": 1,
            "requested_price": 190,
            "status": "created",
        })
        mistock_db.update_managed_order(
            order_id,
            status="failed",
            last_error="Kiwoom ust20000 failed: 종목 정보가 없습니다[1903:종목 정보가 없습니다.]",
        )
        other_id = mistock_db.create_managed_order({
            "client_order_key": "insufficient-bby",
            "symbol": "BBY",
            "action": "buy",
            "requested_qty": 1,
            "requested_price": 80,
            "status": "created",
        })
        mistock_db.update_managed_order(
            other_id,
            status="failed",
            last_error="[2000](RC4025:모의투자 주문가능금액을 확인하세요.)",
        )

        self.assertEqual(scheduler._broker_unsupported_symbols(), {"AVB"})

    def test_successful_broker_submission_is_accepted_without_local_fill(self):
        object.__setattr__(config, "trading_env", "demo")
        object.__setattr__(config, "dry_run", False)

        result = self._place(
            _Broker(), strategy_id="us-alpha", approval_id=7, client_order_key="accepted-1"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["broker_order_no"], "US-100")
        self.assertEqual(mistock_db.rows("SELECT * FROM trades"), [])
        self.assertEqual(mistock_db.rows("SELECT * FROM holdings"), [])
        order = mistock_db.row("SELECT * FROM managed_orders WHERE id=?", (result["managed_order_id"],))
        self.assertEqual(order["status"], "accepted")
        self.assertEqual(order["broker_order_no"], "US-100")
        self.assertEqual(order["filled_qty"], 0)
        self.assertIsNone(order["avg_fill_price"])

    def test_only_unsupported_demo_order_uses_shadow_fill(self):
        object.__setattr__(config, "trading_env", "demo")
        object.__setattr__(config, "dry_run", False)
        broker = _Broker({"rt_cd": "1", "msg1": "모의투자에서는 해당업무가 제공되지 않습니다."})

        result = self._place(broker, client_order_key="demo-shadow")

        self.assertEqual(result["status"], "demo_local_filled")
        self.assertEqual(mistock_db.row("SELECT order_status FROM trades")["order_status"], "demo_local_filled")
        self.assertEqual(mistock_db.row("SELECT qty FROM holdings WHERE symbol='AAPL'")["qty"], 2)
        order = mistock_db.row("SELECT * FROM managed_orders WHERE client_order_key='demo-shadow'")
        self.assertEqual(order["filled_qty"], 2)
        self.assertEqual(order["avg_fill_price"], 100)

    def test_simulation_is_filled_and_updates_trade_and_holding(self):
        object.__setattr__(config, "trading_env", "sim")
        object.__setattr__(config, "dry_run", False)
        mistock_db.set_setting("cash", "10000")

        result = self._place(_Broker(), client_order_key="simulation-fill")

        self.assertEqual(result["status"], "filled")
        self.assertEqual(mistock_db.row("SELECT order_status FROM trades")["order_status"], "filled")
        self.assertEqual(mistock_db.row("SELECT qty FROM holdings WHERE symbol='AAPL'")["qty"], 2)

    def test_broker_rejection_and_exception_have_distinct_statuses(self):
        object.__setattr__(config, "trading_env", "demo")
        object.__setattr__(config, "dry_run", False)

        rejected = self._place(
            _Broker({"rt_cd": "1", "msg1": "broker rejected"}), client_order_key="rejected"
        )
        failed = self._place(
            _Broker(error=RuntimeError("transport failed")), client_order_key="failed"
        )

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(mistock_db.row("SELECT status FROM managed_orders WHERE client_order_key='rejected'")["status"], "rejected")
        self.assertEqual(mistock_db.row("SELECT status FROM managed_orders WHERE client_order_key='failed'")["status"], "failed")
        self.assertEqual(mistock_db.rows("SELECT * FROM trades"), [])

    def test_list_summary_and_sync_never_estimate_fills(self):
        for key, status in (("a", "accepted"), ("b", "accepted"), ("c", "rejected")):
            mistock_db.create_managed_order({
                "client_order_key": key, "symbol": "AAPL", "action": "buy",
                "requested_qty": 1, "requested_price": 100, "status": status,
            })

        listed = mistock.mistock_managed_orders(status="accepted")
        sync = mistock.mistock_managed_order_sync_status()

        self.assertEqual(listed["count"], 2)
        self.assertEqual(listed["status_summary"], {"accepted": 2, "rejected": 1})
        self.assertEqual(listed["summary"], listed["status_summary"])
        self.assertEqual(listed["source"], "mistock_managed_orders")
        self.assertTrue(listed["as_of"])
        self.assertTrue(listed["read_only"])
        self.assertEqual(listed["sync"]["availability"], "unavailable")
        self.assertFalse(listed["sync"]["estimated_fills"])
        self.assertEqual(sync["availability"], "unavailable")
        self.assertFalse(sync["estimated_fills"])
        self.assertFalse(sync["mutated"])

    def test_cancel_and_revise_update_the_managed_order_state(self):
        object.__setattr__(config, "trading_env", "demo")
        object.__setattr__(config, "dry_run", False)
        order_id = mistock_db.create_managed_order({
            "client_order_key": "modify", "broker_order_no": "US-200",
            "symbol": "AAPL", "action": "buy", "requested_qty": 2,
            "requested_price": 100, "status": "accepted",
        })
        with patch.object(trader, "_get_broker_client", return_value=_Broker()):
            cancelled = trader.cancel_order("AAPL", "US-200", qty=2)
            revised = trader.revise_order("AAPL", "US-200", qty=3, price=101.5)

        self.assertEqual(cancelled["status"], "submitted")
        self.assertEqual(revised["status"], "submitted")
        row = mistock_db.row("SELECT * FROM managed_orders WHERE id=?", (order_id,))
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["requested_qty"], 3)
        self.assertEqual(row["requested_price"], 101.5)


if __name__ == "__main__":
    unittest.main()
