import unittest
from unittest.mock import MagicMock, patch
from src.api.kis_websocket import KISWebSocketClient

class TestKISWebSocketClient(unittest.TestCase):
    def setUp(self):
        # Create an instance with notify_errors disabled for testing isolation
        self.client = KISWebSocketClient(notify_errors=False)

    @patch("src.api.kis_websocket.requests.post")
    def test_get_approval_key_caches_and_fetches(self, mock_post):
        # Prepare response
        mock_response = MagicMock()
        mock_response.json.return_value = {"approval_key": "test_ws_approval_key_123"}
        mock_post.return_value = mock_response

        # Fetch key
        key1 = self.client.get_approval_key()
        self.assertEqual(key1, "test_ws_approval_key_123")
        
        # Second call should use cache
        key2 = self.client.get_approval_key()
        self.assertEqual(key2, "test_ws_approval_key_123")
        
        # Requests should only be posted once due to cache
        mock_post.assert_called_once()

    @patch("src.api.kis_websocket.slack_order")
    def test_process_order_execution_parses_and_sends_slack_card(self, mock_slack_order):
        # Mock payload: 고객ID^계좌번호^주문번호^원주문번호^매도매수구분^주문구분^종목코드^주문수량^주문가격^체결수량^체결단가^체결시간...체결구분
        # Fields:
        # 4: 매도매수구분 (02 = buy)
        # 6: 종목코드 (005930)
        # 9: 체결수량 (10)
        # 10: 체결단가 (72000)
        # 15: 체결구분 (2 = 체결)
        sample_payload = "USR100^50012345^0001^0000^02^00^005930^10^72000^10^72000^093000^Y^0^0^2^CHK001"
        
        self.client._process_order_execution(sample_payload)
        
        # Should call slack_order with parsed values
        mock_slack_order.assert_called_once_with(
            name="실시간 체결 통보 (005930)",
            symbol="005930",
            action="buy",
            qty=10.0,
            price=72000.0,
            reason="KIS WebSocket 실시간 주문 체결 완료",
            ok=True,
            indicators={"rsi": 0.0, "sma20": 0.0, "sma60": 0.0, "rt": 0.0}
        )

    @patch("src.api.kis_websocket.slack_order")
    def test_process_order_execution_ignores_acceptance_events(self, mock_slack_order):
        # Execution type is "4" (acceptance, not filled execution)
        sample_payload = "USR100^50012345^0001^0000^02^00^005930^10^72000^0^0^093000^Y^0^0^4^CHK001"
        
        self.client._process_order_execution(sample_payload)
        
        # Slack card should NOT be sent for pure acceptance events
        mock_slack_order.assert_not_called()

    @patch("src.api.kis_websocket.slack_order")
    def test_process_order_execution_handles_invalid_payloads_gracefully(self, mock_slack_order):
        # Too short payload
        short_payload = "USR100^50012345^0001"
        self.client._process_order_execution(short_payload)
        mock_slack_order.assert_not_called()

        # Non-numeric quantity/price fields
        bad_numbers_payload = "USR100^50012345^0001^0000^02^00^005930^abc^xyz^abc^xyz^093000^Y^0^0^2^CHK001"
        self.client._process_order_execution(bad_numbers_payload)
        mock_slack_order.assert_not_called()

    def test_process_realtime_quote_stores_latest_quote(self):
        payload = "005930^093000^72000^2^500^0.70^72000^72100^71900^72000^70000^1000^2000^123456"

        self.client._process_realtime_quote(payload)

        quote = self.client.last_quotes["005930"]
        self.assertEqual(quote["symbol"], "005930")
        self.assertEqual(quote["time"], "093000")
        self.assertEqual(quote["price"], 72000.0)
        self.assertEqual(quote["volume"], 123456.0)

    def test_process_realtime_orderbook_stores_latest_orderbook(self):
        payload = "005930^093000^0^72100^72000^72200^71900"

        self.client._process_realtime_orderbook(payload)

        orderbook = self.client.last_orderbooks["005930"]
        self.assertEqual(orderbook["ask1"], 72100.0)
        self.assertEqual(orderbook["bid1"], 72000.0)

    def test_subscribe_quote_and_orderbook_register_expected_tr_ids(self):
        self.client.subscribe_quote("005930")
        self.client.subscribe_orderbook("000660")

        self.assertIn(("H0STCNT0", "005930"), self.client.active_subscriptions)
        self.assertIn(("H0STASP0", "000660"), self.client.active_subscriptions)

    def test_on_open_logs_missing_hts_id_as_info(self):
        with patch("src.api.kis_websocket.config.kistock_hts_id", ""), patch.object(
            self.client, "subscribe"
        ) as subscribe, patch("src.api.kis_websocket.logger.warning") as warning, patch(
            "src.api.kis_websocket.logger.info"
        ) as info:
            self.client.on_open(MagicMock())

        subscribe.assert_not_called()
        warning.assert_not_called()
        self.assertTrue(any("KISTOCK_HTS_ID is not configured" in call.args[0] for call in info.call_args_list))

    @patch("src.api.kis_websocket.slack_error")
    def test_on_error_sends_slack_when_enabled(self, mock_slack_error):
        client = KISWebSocketClient(notify_errors=True)

        client.on_error(None, "fatal websocket error")

        self.assertEqual(client.last_error, "fatal websocket error")
        mock_slack_error.assert_called_once_with(
            "KIS WebSocket error: fatal websocket error"
        )

    @patch("src.api.kis_websocket.slack_error")
    def test_on_error_treats_remote_host_lost_as_recoverable(self, mock_slack_error):
        client = KISWebSocketClient(notify_errors=True)

        with patch("src.api.kis_websocket.logger.error") as log_error, patch(
            "src.api.kis_websocket.logger.info"
        ) as log_info:
            client.on_error(None, "Connection to remote host was lost.")

        self.assertEqual(client.last_error, "Connection to remote host was lost.")
        log_error.assert_not_called()
        mock_slack_error.assert_not_called()
        self.assertTrue(any("Recoverable disconnect" in call.args[0] for call in log_info.call_args_list))

    @patch("src.api.kis_websocket.slack_error")
    def test_on_error_does_not_send_slack_when_disabled(self, mock_slack_error):
        self.client.on_error(None, "Connection to remote host was lost.")

        self.assertEqual(self.client.last_error, "Connection to remote host was lost.")
        mock_slack_error.assert_not_called()

    @patch("src.api.kis_websocket.slack_error")
    def test_on_error_throttles_repeated_slack_notifications(self, mock_slack_error):
        client = KISWebSocketClient(notify_errors=True)

        client.on_error(None, "first")
        client.on_error(None, "second")

        mock_slack_error.assert_called_once_with("KIS WebSocket error: first")
