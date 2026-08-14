import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json

from src.mistock import monitor
from src.mistock.config import config as mistock_config
from src.kis_client import CircuitBreakerState


class MistockMonitorTests(unittest.TestCase):
    def setUp(self):
        # Backup configuration and paths
        self.original_env = mistock_config.trading_env
        self.original_db_path = mistock_config.trade_db_path

    def tearDown(self):
        mistock_config.trading_env = self.original_env
        mistock_config.trade_db_path = self.original_db_path

    @patch("src.mistock.monitor.datetime")
    def test_is_us_market_open_dst(self, mock_datetime):
        # DST Summer Time (month 6, June)
        KST = timezone(timedelta(hours=9))
        
        # 1. 23:00 KST (Open)
        mock_datetime.now.return_value = datetime(2026, 6, 12, 23, 0, tzinfo=KST)
        self.assertTrue(monitor.is_us_market_open())

        # 2. 03:00 KST (Open)
        mock_datetime.now.return_value = datetime(2026, 6, 12, 3, 0, tzinfo=KST)
        self.assertTrue(monitor.is_us_market_open())

        # 3. 12:00 KST (Closed)
        mock_datetime.now.return_value = datetime(2026, 6, 12, 12, 0, tzinfo=KST)
        self.assertFalse(monitor.is_us_market_open())

    @patch("src.mistock.monitor.datetime")
    def test_is_us_market_open_standard_time(self, mock_datetime):
        # Standard Time (month 12, December)
        KST = timezone(timedelta(hours=9))
        
        # 1. 01:00 KST (Open)
        mock_datetime.now.return_value = datetime(2026, 12, 12, 1, 0, tzinfo=KST)
        self.assertTrue(monitor.is_us_market_open())

        # 2. 23:00 KST (Closed - Standard time starts at 23:30)
        mock_datetime.now.return_value = datetime(2026, 12, 12, 23, 0, tzinfo=KST)
        self.assertFalse(monitor.is_us_market_open())

    @patch("src.mistock.monitor.is_us_market_open", return_value=False)
    def test_run_cycle_skipped_outside_market_hours(self, mock_open):
        result = monitor.run_monitoring_cycle()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "outside_market_hours")

    @patch("src.mistock.monitor.is_us_market_open", return_value=True)
    @patch("src.mistock.trader._get_kis_client")
    @patch("src.mistock.monitor.send_mistock_slack")
    def test_run_cycle_alerts_on_circuit_breaker_opened(self, mock_slack, mock_get_client, mock_open):
        mistock_config.trading_env = "demo"
        
        # Mock Circuit Breaker Status
        fake_cb = CircuitBreakerState()
        fake_cb.error_count = 5
        fake_cb.opened_at = datetime.now(timezone.utc)
        
        mock_client = MagicMock()
        mock_client.circuit = fake_cb
        mock_client.config.circuit_max_errors = 5
        mock_client.config.circuit_cooldown_seconds = 60
        mock_get_client.return_value = mock_client

        # Mock scheduler results path not existing to isolate test
        with patch("src.mistock.monitor.Path.exists", return_value=False):
            result = monitor.run_monitoring_cycle()

        self.assertEqual(result["status"], "alerted")
        mock_slack.assert_called_once()
        self.assertIn("🚨 *[미스톡 경보] KIS API 서킷 브레이커 오픈 감지!*", mock_slack.call_args[1]["blocks"][0]["text"]["text"])

    @patch("src.mistock.monitor.is_us_market_open", return_value=True)
    @patch("src.mistock.trader._get_kis_client")
    @patch("src.mistock.monitor.send_mistock_slack")
    def test_run_cycle_healthy_when_no_errors(self, mock_slack, mock_get_client, mock_open):
        mistock_config.trading_env = "demo"
        
        # Healthy CB Status
        fake_cb = CircuitBreakerState()
        mock_client = MagicMock()
        mock_client.circuit = fake_cb
        mock_client.config.circuit_max_errors = 5
        mock_client.config.circuit_cooldown_seconds = 60
        mock_get_client.return_value = mock_client

        with patch("src.mistock.monitor.Path.exists", return_value=False):
            result = monitor.run_monitoring_cycle()

        self.assertEqual(result["status"], "healthy")
        mock_slack.assert_not_called()


if __name__ == "__main__":
    unittest.main()
