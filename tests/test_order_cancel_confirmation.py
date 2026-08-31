import tempfile
import unittest
from unittest.mock import patch

from src.application.orders.models import OrderIntent
from src.application.orders.repository import OrderLedgerRepository
from src.broker.models import OrderSnapshot, OrderStatus
from src.dashboard.routes import stock_order


class OrderCancelConfirmationTests(unittest.TestCase):
    def test_single_order_poll_confirms_cancellation_without_full_sync(self):
        original_db_path = stock_order.trader.config.trade_db_path
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                stock_order.trader.config.trade_db_path = f"{temp_dir}/trades.sqlite"
                stock_order.trader.init_db()
                repository = OrderLedgerRepository(stock_order.trader.connect_db)
                order = repository.create(OrderIntent(
                    client_order_key="cancel-confirm-test",
                    correlation_id="cancel-confirm-correlation",
                    symbol="066970",
                    name="엘앤에프",
                    side="buy",
                    quantity=31,
                    price=134800,
                    broker_order_id="0035136",
                    broker_order_date="2026-08-31",
                ), initial_status="submitted")
                repository.transition(
                    order["id"], "submitted", "cancel_pending", actor="test"
                )

                class FakeBroker:
                    def fetch_order_snapshot(self, order_id, order_date=""):
                        self.request = (order_id, order_date)
                        return OrderSnapshot(
                            broker_order_id=order_id,
                            status=OrderStatus.CANCELED,
                            requested_quantity=31,
                            filled_quantity=0,
                            remaining_quantity=31,
                            raw={"cncl_yn": "Y"},
                        )

                broker = FakeBroker()
                with patch.object(stock_order, "_get_api", return_value=broker), patch.object(
                    stock_order, "_clear_balance_cache"
                ):
                    stock_order._confirm_canceled_order(
                        order["id"], attempts=1, interval_seconds=0
                    )

                detail = repository.detail(order["id"])
                self.assertEqual(detail["status"], "canceled")
                self.assertEqual(broker.request, ("0035136", "20260831"))
                self.assertEqual(detail["events"][-1]["event_type"], "cancel_confirmed")
        finally:
            stock_order.trader.config.trade_db_path = original_db_path

    def test_single_order_poll_times_out_to_broker_unknown(self):
        original_db_path = stock_order.trader.config.trade_db_path
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                stock_order.trader.config.trade_db_path = f"{temp_dir}/trades.sqlite"
                stock_order.trader.init_db()
                repository = OrderLedgerRepository(stock_order.trader.connect_db)
                order = repository.create(OrderIntent(
                    client_order_key="cancel-timeout-test",
                    correlation_id="cancel-timeout-correlation",
                    symbol="005930",
                    side="buy",
                    quantity=1,
                    broker_order_id="0000001",
                    broker_order_date="2026-08-31",
                ), initial_status="submitted")
                repository.transition(order["id"], "submitted", "cancel_pending")

                class FakeBroker:
                    def fetch_order_snapshot(self, order_id, order_date=""):
                        return OrderSnapshot(
                            broker_order_id=order_id,
                            status=OrderStatus.OPEN,
                            requested_quantity=1,
                            remaining_quantity=1,
                        )

                with patch.object(stock_order, "_get_api", return_value=FakeBroker()):
                    stock_order._confirm_canceled_order(
                        order["id"], attempts=2, interval_seconds=0
                    )

                detail = repository.detail(order["id"])
                self.assertEqual(detail["status"], "broker_unknown")
                self.assertIn("timed out", detail["events"][-1]["reason"])
        finally:
            stock_order.trader.config.trade_db_path = original_db_path


if __name__ == "__main__":
    unittest.main()
