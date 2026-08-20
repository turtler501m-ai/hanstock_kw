import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from src.broker.kiwoom_client import KiwoomApiError, KiwoomRestClient, RequestThrottle


def response(payload, headers=None):
    item = Mock()
    item.json.return_value = payload
    item.headers = headers or {}
    item.raise_for_status.return_value = None
    return item


class KiwoomRestClientTests(unittest.TestCase):
    def setUp(self):
        KiwoomRestClient.clear_shared_token_cache()
        self.now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        self.session = Mock()

    def client(self, **kwargs):
        return KiwoomRestClient("app", "very-secret", session=self.session, now=lambda: self.now, **kwargs)

    def test_mock_and_live_base_urls(self):
        self.assertEqual(self.client().base_url, "https://mockapi.kiwoom.com")
        self.assertEqual(self.client(environment="live").base_url, "https://api.kiwoom.com")

    def test_token_is_cached_and_secret_is_not_in_repr(self):
        self.session.post.return_value = response({"token": "TOKEN", "expires_in": 3600})
        client = self.client()
        self.assertEqual(client.get_access_token(), "TOKEN")
        self.assertEqual(client.get_access_token(), "TOKEN")
        self.assertEqual(self.session.post.call_count, 1)
        self.assertNotIn("very-secret", repr(client))

    def test_token_is_shared_between_clients_with_same_credentials(self):
        self.session.post.return_value = response({"token": "TOKEN", "expires_in": 3600})
        first = self.client()
        second = self.client()

        self.assertEqual(first.get_access_token(), "TOKEN")
        self.assertEqual(second.get_access_token(), "TOKEN")
        self.assertEqual(self.session.post.call_count, 1)

    def test_expiring_token_is_refreshed(self):
        self.session.post.side_effect = [
            response({"token": "OLD", "expires_in": 60}), response({"token": "NEW", "expires_in": 60})
        ]
        client = self.client()
        self.assertEqual(client.get_access_token(), "OLD")
        self.now += timedelta(seconds=31)
        self.assertEqual(client.get_access_token(), "NEW")

    def test_post_sends_required_headers_and_json(self):
        self.session.post.side_effect = [response({"token": "TOKEN", "expires_in": 3600}), response({"price": "10"})]
        page = self.client().post("api/dostk/stkinfo", api_id="ka10001", body={"stk_cd": "005930"})
        self.assertEqual(page.data["price"], "10")
        call = self.session.post.call_args_list[1]
        self.assertEqual(call.kwargs["headers"]["authorization"], "Bearer TOKEN")
        self.assertEqual(call.kwargs["headers"]["api-id"], "ka10001")
        self.assertEqual(call.kwargs["json"], {"stk_cd": "005930"})

    def test_continuation_headers_are_forwarded(self):
        self.session.post.side_effect = [
            response({"token": "TOKEN", "expires_in": 3600}),
            response({"rows": [1]}, {"cont-yn": "Y", "next-key": "next"}),
            response({"rows": [2]}, {"cont-yn": "N"}),
        ]
        pages = self.client().post_all_pages("api/test", api_id="ka-test")
        self.assertEqual([p.data["rows"] for p in pages], [[1], [2]])
        headers = self.session.post.call_args_list[2].kwargs["headers"]
        self.assertEqual((headers["cont-yn"], headers["next-key"]), ("Y", "next"))

    def test_invalid_token_response_raises_without_leaking_secret(self):
        self.session.post.return_value = response({})
        with self.assertRaisesRegex(KiwoomApiError, "did not contain") as caught:
            self.client().get_access_token()
        self.assertNotIn("very-secret", str(caught.exception))

    def test_broker_error_code_raises_sanitized_error(self):
        self.session.post.side_effect = [
            response({"token": "TOKEN", "expires_in": 3600}),
            response({"return_code": 101, "return_msg": "invalid request"}),
        ]
        with self.assertRaisesRegex(KiwoomApiError, "invalid request") as caught:
            self.client().post("api/test", api_id="ka-test")
        self.assertNotIn("very-secret", str(caught.exception))

    def test_query_reauthenticates_once_when_broker_invalidates_cached_token(self):
        self.session.post.side_effect = [
            response({"token": "OLD", "expires_in": 3600}),
            response({"return_code": 8005, "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]"}),
            response({"token": "NEW", "expires_in": 3600}),
            response({"price": "10"}),
        ]

        page = self.client().post("api/test", api_id="ka-test")

        self.assertEqual(page.data["price"], "10")
        self.assertEqual(self.session.post.call_args_list[1].kwargs["headers"]["authorization"], "Bearer OLD")
        self.assertEqual(self.session.post.call_args_list[3].kwargs["headers"]["authorization"], "Bearer NEW")

    def test_order_does_not_retry_after_invalid_token_response(self):
        self.session.post.side_effect = [
            response({"token": "OLD", "expires_in": 3600}),
            response({"return_code": 8005, "return_msg": "8005:Token이 유효하지 않습니다"}),
        ]

        with self.assertRaisesRegex(KiwoomApiError, "8005"):
            self.client().post("api/order", api_id="kt10000", request_kind="order")

        self.assertEqual(self.session.post.call_count, 2)


class RequestThrottleTests(unittest.TestCase):
    def test_waits_per_lane(self):
        clock_value = [10.0]
        sleeps = []

        def sleep(delay):
            sleeps.append(delay)
            clock_value[0] += delay

        limiter = RequestThrottle(clock=lambda: clock_value[0], sleep=sleep)
        limiter.wait("mock:ka1", 1.0)
        limiter.wait("mock:ka1", 1.0)
        limiter.wait("mock:ka2", 1.0)
        self.assertEqual(sleeps, [1.0])

    def test_live_interval_supports_five_requests_per_second(self):
        clock_value = [0.0]
        sleeps = []

        def sleep(delay):
            sleeps.append(delay)
            clock_value[0] += delay

        limiter = RequestThrottle(clock=lambda: clock_value[0], sleep=sleep)
        limiter.wait("live:query", 0.2)
        limiter.wait("live:query", 0.2)
        self.assertEqual(sleeps, [0.2])


if __name__ == "__main__":
    unittest.main()
