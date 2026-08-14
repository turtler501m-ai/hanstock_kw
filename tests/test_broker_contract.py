import os
import unittest
from unittest.mock import Mock, patch

from src.broker.base import DomesticStockBroker
from src.broker.factory import create_domestic_stock_broker, selected_domestic_stock_broker
from src.broker.kis_adapter import KISBrokerAdapter
from src.broker.models import OrderRequest, OrderSide, OrderStatus


class BrokerContractTests(unittest.TestCase):
    def test_factory_defaults_to_kis(self):
        broker = create_domestic_stock_broker(client=Mock())
        self.assertIsInstance(broker, KISBrokerAdapter)
        self.assertIsInstance(broker, DomesticStockBroker)

    def test_factory_uses_injected_kis_client_factory(self):
        client = Mock()
        factory = Mock(return_value=client)
        broker = create_domestic_stock_broker(kis_client_factory=factory, notify_errors=True)
        factory.assert_called_once_with(notify_errors=True)
        self.assertIs(broker.client, client)

    def test_selected_broker_rejects_unknown_value(self):
        with self.assertRaisesRegex(ValueError, "Unsupported domestic stock broker"):
            selected_domestic_stock_broker("unknown")

    def test_selected_broker_reads_environment(self):
        with patch.dict(os.environ, {"DOMESTIC_STOCK_BROKER": "KIWOOM"}):
            self.assertEqual(selected_domestic_stock_broker(), "kiwoom")

    def test_kis_adapter_normalizes_balance_and_retains_raw(self):
        client = Mock()
        client.get_balance.return_value = {
            "output1": [{"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "3", "ord_psbl_qty": "2", "pchs_avg_pric": "70000", "prpr": "71000", "evlu_amt": "213000"}],
            "output2": [{"dnca_tot_amt": "1000000", "tot_evlu_amt": "1213000", "scts_evlu_amt": "213000"}],
        }
        balance = KISBrokerAdapter(client).fetch_balance()
        self.assertEqual(balance.cash, 1_000_000)
        self.assertEqual(balance.holdings[0].symbol, "005930")
        self.assertEqual(balance.holdings[0].sellable_quantity, 2)
        self.assertEqual(balance.raw["output1"][0]["pdno"], "005930")

    def test_kis_adapter_normalizes_order_result(self):
        client = Mock()
        client.place_order.return_value = {"rt_cd": "0", "msg1": "주문 접수", "output": {"ODNO": "12345"}}
        result = KISBrokerAdapter(client).submit_order(OrderRequest("005930", OrderSide.BUY, 2, 70000))
        self.assertTrue(result.success)
        self.assertEqual(result.broker_order_id, "12345")
        self.assertEqual(result.status, OrderStatus.SUBMITTED)
        client.place_order.assert_called_once_with("005930", "buy", 70000, 2)

    def test_legacy_surface_delegates_during_migration(self):
        client = Mock()
        client.get_quote.return_value = {"current": 71000}
        self.assertEqual(KISBrokerAdapter(client).get_quote("005930"), {"current": 71000})


if __name__ == "__main__":
    unittest.main()
