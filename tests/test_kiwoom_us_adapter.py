import unittest
from unittest.mock import Mock, patch

from src.broker.kiwoom_client import KiwoomPage
from src.broker.kiwoom_us_adapter import KiwoomUSStockAdapter
from src.mistock import trader
from src.mistock.config import config as mistock_config


class KiwoomUSStockAdapterTests(unittest.TestCase):
    def test_balance_is_mapped_to_existing_mistock_shape(self):
        client = Mock()
        client.post_all_pages.return_value = [KiwoomPage(data={
            "crnc_code": "USD",
            "tot_evlt_amt": "12,500.50",
            "tot_pl_amt": "250.50",
            "result_list": [{
                "stk_cd": "AAPL", "frgn_stk_nm": "Apple", "poss_qty": "3",
                "frgn_stk_book_uv": "180.1", "now_pric": "190.2",
                "evlt_amt": "570.6", "pl_amt": "30.3", "pl_rt": "5.61",
            }],
        })]
        client.post.return_value = KiwoomPage(data={"result_list": [{"crnc_code": "USD", "fc_entra": "1000", "fc_ord_alowa": "900"}]})
        adapter = KiwoomUSStockAdapter(client, account_no="12345678")

        result = adapter.get_overseas_balance()

        self.assertEqual(result["output1"][0]["pdno"], "AAPL")
        self.assertEqual(result["output1"][0]["cblc_qty13"], "3")
        self.assertEqual(result["output2"]["frcr_evlu_tota"], "12,500.50")
        self.assertEqual(result["output2"]["frcr_drwg_psbl_amt"], "900")
        self.assertEqual(result["_broker"], "kiwoom")
        client.post_all_pages.assert_called_once_with(
            "/api/us/acnt", api_id="ust21070", body={"stex_tp": "", "stk_cd": ""}
        )

    def test_multiple_pages_are_combined(self):
        client = Mock()
        client.post_all_pages.return_value = [
            KiwoomPage(data={"tot_evlt_amt": "10", "result_list": [{"stk_cd": "AAPL", "poss_qty": "1"}]}),
            KiwoomPage(data={"result_list": [{"stk_cd": "MSFT", "poss_qty": "2"}]}),
        ]
        client.post.return_value = KiwoomPage(data={"result_list": []})
        result = KiwoomUSStockAdapter(client).get_overseas_balance()
        self.assertEqual([row["pdno"] for row in result["output1"]], ["AAPL", "MSFT"])

    def test_demo_buy_uses_official_kiwoom_us_order_tr(self):
        client = Mock()
        client.post.return_value = KiwoomPage(data={"ord_no": "123456789", "return_msg": "accepted"})
        adapter = KiwoomUSStockAdapter(client, order_submission_enabled=True)

        result = adapter.place_overseas_order("AAPL", "buy", 190.25, 2)

        self.assertEqual(result["rt_cd"], "0")
        self.assertEqual(result["output"]["ODNO"], "123456789")
        client.post.assert_called_once_with(
            "/api/us/ordr",
            api_id="ust20000",
            body={"stex_tp": "ND", "stk_cd": "AAPL", "ord_qty": "2", "ord_uv": "190.25", "trde_tp": "00"},
            request_kind="order",
        )

    def test_order_fails_closed_until_submission_is_enabled(self):
        adapter = KiwoomUSStockAdapter(Mock())
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            adapter.place_overseas_order("AAPL", "buy", 190, 1)


class KiwoomUSStockWiringTests(unittest.TestCase):
    def setUp(self):
        self.original_broker = mistock_config.stock_broker
        self.original_env = mistock_config.trading_env
        self.original_dry_run = mistock_config.dry_run
        trader._kis_client_cache = None

    def tearDown(self):
        mistock_config.stock_broker = self.original_broker
        mistock_config.trading_env = self.original_env
        mistock_config.dry_run = self.original_dry_run
        trader._kis_client_cache = None

    @patch("src.online_access.require_online_access")
    @patch("src.config.config")
    @patch("src.broker.kiwoom_client.KiwoomRestClient")
    def test_demo_credentials_select_distinct_us_client(self, client_type, settings, _online):
        mistock_config.stock_broker = "kiwoom"
        mistock_config.trading_env = "demo"
        mistock_config.dry_run = True
        settings.kiwoom_us_demo_app_key = "us-key"
        settings.kiwoom_us_demo_app_secret = "us-secret"
        settings.kiwoom_us_demo_account = "us-account"

        result = trader._get_kis_client()

        client_type.assert_called_once_with("us-key", "us-secret", environment="mock")
        self.assertEqual(result.account_no, "us-account")
        self.assertFalse(result.order_submission_enabled)
        self.assertFalse(trader.broker_submission_available())

    @patch("src.online_access.require_online_access")
    @patch("src.config.config")
    @patch("src.broker.kiwoom_client.KiwoomRestClient")
    def test_demo_order_adapter_activates_only_when_dry_run_is_off(self, client_type, settings, _online):
        mistock_config.stock_broker = "kiwoom"
        mistock_config.trading_env = "demo"
        mistock_config.dry_run = False
        settings.kiwoom_us_demo_app_key = "us-key"
        settings.kiwoom_us_demo_app_secret = "us-secret"
        settings.kiwoom_us_demo_account = "us-account"

        result = trader._get_kis_client()

        self.assertTrue(result.order_submission_enabled)
        self.assertTrue(trader.broker_submission_available())

    @patch("src.online_access.require_online_access")
    @patch("src.config.config")
    def test_missing_demo_credentials_fail_closed(self, settings, _online):
        mistock_config.stock_broker = "kiwoom"
        mistock_config.trading_env = "demo"
        settings.kiwoom_us_demo_app_key = ""
        settings.kiwoom_us_demo_app_secret = ""
        settings.kiwoom_us_demo_account = ""
        with self.assertRaisesRegex(RuntimeError, "required"):
            trader._get_kis_client()

    @patch("src.online_access.require_online_access")
    def test_real_environment_is_not_activated(self, _online):
        mistock_config.stock_broker = "kiwoom"
        mistock_config.trading_env = "real"
        with self.assertRaisesRegex(RuntimeError, "demo-only"):
            trader._get_kis_client()


if __name__ == "__main__":
    unittest.main()
