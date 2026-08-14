import base64
import unittest
from unittest.mock import Mock, patch

from src.api.quantconnect_api import QuantConnectAPI, QuantConnectCredentials


class QuantConnectAPITests(unittest.TestCase):
    def test_headers_use_timestamped_hash_without_plain_token(self):
        api = QuantConnectAPI(QuantConnectCredentials("123", "secret-token"))

        with patch("src.api.quantconnect_api.time.time", return_value=1_700_000_000):
            headers = api.headers()

        self.assertEqual(headers["Timestamp"], "1700000000")
        decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode("ascii")
        self.assertTrue(decoded.startswith("123:"))
        self.assertNotIn("secret-token", decoded)

    def test_authenticate_skips_request_when_credentials_missing(self):
        api = QuantConnectAPI(QuantConnectCredentials("", "token"))

        with patch("src.api.quantconnect_api.requests.post") as post:
            result = api.authenticate()

        self.assertFalse(result["success"])
        self.assertFalse(result["configured"])
        post.assert_not_called()

    def test_authenticate_posts_to_quantconnect_endpoint(self):
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"success": True}
        api = QuantConnectAPI(QuantConnectCredentials("123", "token"))

        with patch("src.api.quantconnect_api.requests.post", return_value=response) as post:
            result = api.authenticate()

        self.assertTrue(result["success"])
        post.assert_called_once()
        self.assertTrue(post.call_args.args[0].endswith("/authenticate"))

    def test_authenticate_reports_success_false_errors(self):
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"success": False, "errors": ["Hash does not match"]}
        api = QuantConnectAPI(QuantConnectCredentials("123", "token"))

        with patch("src.api.quantconnect_api.requests.post", return_value=response):
            result = api.authenticate()

        self.assertFalse(result["success"])
        self.assertIn("Hash does not match", result["error"])

    def test_create_live_command_posts_project_command(self):
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"success": True}
        api = QuantConnectAPI(QuantConnectCredentials("123", "token", "456"))

        with patch("src.api.quantconnect_api.requests.post", return_value=response) as post:
            result = api.create_live_command("456", {"command_type": "order"})

        self.assertTrue(result["success"])
        self.assertTrue(post.call_args.args[0].endswith("/live/commands/create"))
        self.assertEqual(post.call_args.kwargs["json"]["projectId"], 456)

    def test_create_live_algorithm_posts_paper_payload(self):
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"success": True, "deployId": "L-123"}
        api = QuantConnectAPI(QuantConnectCredentials("123", "token", "456"))

        with patch("src.api.quantconnect_api.requests.post", return_value=response) as post:
            result = api.create_live_algorithm("456", "compile-1", "node-1", parameters={"MAX_CONTRACTS": "3"})

        self.assertTrue(result["success"])
        self.assertTrue(post.call_args.args[0].endswith("/live/create"))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["projectId"], 456)
        self.assertEqual(body["compileId"], "compile-1")
        self.assertEqual(body["nodeId"], "node-1")
        self.assertEqual(body["brokerage"]["id"], "QuantConnectBrokerage")
        self.assertEqual(body["brokerage"]["environment"], "live-paper")
        self.assertEqual(body["parameters"]["MAX_CONTRACTS"], "3")


if __name__ == "__main__":
    unittest.main()
