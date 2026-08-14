import os
import unittest
from unittest.mock import Mock, patch

from src.broker.base import DomesticStockBroker
from src.broker.factory import create_domestic_stock_broker, selected_domestic_stock_broker
from src.broker.kiwoom_adapter import KiwoomBrokerAdapter
from src.broker.models import OrderRequest, OrderSide, OrderStatus


class BrokerContractTests(unittest.TestCase):
    def test_factory_defaults_to_kiwoom(self):
        broker = create_domestic_stock_broker(client=Mock())
        self.assertIsInstance(broker, KiwoomBrokerAdapter)
        self.assertIsInstance(broker, DomesticStockBroker)

    def test_factory_uses_injected_kiwoom_client(self):
        client = Mock()
        broker = create_domestic_stock_broker(client=client, notify_errors=True)
        self.assertIs(broker.client, client)

    def test_selected_broker_rejects_unknown_value(self):
        with self.assertRaisesRegex(ValueError, "Unsupported domestic stock broker"):
            selected_domestic_stock_broker("unknown")

    def test_selected_broker_reads_environment(self):
        with patch.dict(os.environ, {"DOMESTIC_STOCK_BROKER": "KIWOOM"}):
            self.assertEqual(selected_domestic_stock_broker(), "kiwoom")

    def test_factory_builds_kiwoom_adapter_from_injected_transport(self):
        client = Mock()
        broker = create_domestic_stock_broker(
            "kiwoom", client=client, order_submission_enabled=True
        )
        self.assertEqual(broker.broker_name, "kiwoom")
        self.assertIs(broker.client, client)
        self.assertTrue(broker.order_submission_enabled)

    def test_factory_rejects_missing_kiwoom_credentials(self):
        settings = Mock(
            kiwoom_trading_env="demo",
            trading_env="demo",
            kiwoom_domestic_demo_app_key="",
            kiwoom_domestic_demo_app_secret="",
        )
        with self.assertRaisesRegex(ValueError, "App Key and App Secret are required"):
            create_domestic_stock_broker("kiwoom", settings=settings)

    def test_factory_rejects_kiwoom_and_application_environment_mismatch(self):
        settings = Mock(
            kiwoom_trading_env="real",
            trading_env="demo",
            kiwoom_domestic_real_app_key="key",
            kiwoom_domestic_real_app_secret="secret",
        )
        with self.assertRaisesRegex(ValueError, "must match TRADING_ENV"):
            create_domestic_stock_broker("kiwoom", settings=settings)

    @unittest.skip("legacy transport fixture removed")
    def test_removed_adapter_normalizes_balance_and_retains_raw(self):
        client = Mock()
        client.get_balance.return_value = {
            "output1": [{"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "3", "ord_psbl_qty": "2", "pchs_avg_pric": "70000", "prpr": "71000", "evlu_amt": "213000"}],
            "output2": [{"dnca_tot_amt": "1000000", "tot_evlu_amt": "1213000", "scts_evlu_amt": "213000"}],
        }
        balance = KiwoomBrokerAdapter(client).fetch_balance()
        self.assertEqual(balance.cash, 1_000_000)
        self.assertEqual(balance.holdings[0].symbol, "005930")
        self.assertEqual(balance.holdings[0].sellable_quantity, 2)
        self.assertEqual(balance.raw["output1"][0]["pdno"], "005930")

    @unittest.skip("legacy transport fixture removed")
    def test_removed_adapter_normalizes_order_result(self):
        client = Mock()
        client.place_order.return_value = {"rt_cd": "0", "msg1": "주문 접수", "output": {"ODNO": "12345"}}
        result = KiwoomBrokerAdapter(client).submit_order(OrderRequest("005930", OrderSide.BUY, 2, 70000))
        self.assertTrue(result.success)
        self.assertEqual(result.broker_order_id, "12345")
        self.assertEqual(result.status, OrderStatus.SUBMITTED)
        client.place_order.assert_called_once_with("005930", "buy", 70000, 2)

    def test_kiwoom_quote_contract(self):
        client = Mock()
        client.post.return_value = {"stk_cd": "005930", "cur_prc": "+71000"}
        quote = KiwoomBrokerAdapter(client).fetch_quote("005930")
        self.assertEqual(quote.current_price, 71000)


if __name__ == "__main__":
    unittest.main()
