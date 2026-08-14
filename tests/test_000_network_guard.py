import os
import unittest

import requests

from tests.network_guard import install_network_guard


install_network_guard()

from src.config import config


class NetworkGuardTests(unittest.TestCase):
    def test_unit_test_environment_is_isolated(self):
        self.assertEqual(os.environ.get("HANSTOCK_TESTING"), "1")
        self.assertEqual(os.environ.get("SLACK_WEBHOOK_URL"), "")
        self.assertEqual(os.environ.get("KIS_WEBSOCKET_ENABLED"), "false")
        self.assertEqual(config.kistock_app_key, "")
        self.assertEqual(config.kistock_app_secret, "")

    def test_external_http_is_blocked(self):
        with self.assertRaisesRegex(AssertionError, "external network access is forbidden"):
            requests.get("https://example.com", timeout=1)


if __name__ == "__main__":
    unittest.main()
