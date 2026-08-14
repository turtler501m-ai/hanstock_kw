import unittest
from unittest.mock import patch

from src.notifier import slack


class SlackRoutingTests(unittest.TestCase):
    def test_send_mistock_slack_uses_mistock_webhook_only(self):
        with patch.object(slack.config, "slack_webhook_url", "https://example.test/hanstock"), \
                patch.object(slack.config, "mistock_slack_webhook_url", "https://example.test/mistock"), \
                patch.object(slack, "post_slack_payload") as post:
            slack.send_mistock_slack(text="mistock")

        self.assertEqual(post.call_args.kwargs["webhook_url"], "https://example.test/mistock")

    def test_mistock_order_uses_mistock_webhook_only(self):
        with patch.object(slack.config, "slack_webhook_url", "https://example.test/hanstock"), \
                patch.object(slack.config, "mistock_slack_webhook_url", "https://example.test/mistock"), \
                patch.object(slack, "post_slack_payload") as post:
            slack.mistock_slack_order(
                name="Amazon",
                symbol="AMZN",
                action="buy",
                qty=1,
                price=100,
                reason="test",
                ok=True,
                indicators={},
            )

        self.assertEqual(post.call_args.args[0], "https://example.test/mistock")


if __name__ == "__main__":
    unittest.main()
