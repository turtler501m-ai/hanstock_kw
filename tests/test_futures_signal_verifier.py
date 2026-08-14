from datetime import datetime, timezone
import unittest

from src.futures_signals import FuturesSignal, OhlcCandle, verify_signal


class FuturesSignalVerifierTests(unittest.TestCase):
    def _signal(self, direction="long"):
        if direction == "long":
            return FuturesSignal(
                id="s1",
                source="telegram",
                source_message_id="1",
                received_at=None,
                raw_text="",
                symbol="MNQM26",
                direction="long",
                entry=100.0,
                stop_loss=95.0,
                take_profits=(105.0, 110.0),
            )
        return FuturesSignal(
            id="s2",
            source="telegram",
            source_message_id="2",
            received_at=None,
            raw_text="",
            symbol="MNQM26",
            direction="short",
            entry=100.0,
            stop_loss=105.0,
            take_profits=(95.0, 90.0),
        )

    def test_long_take_profit_before_stop_loss(self):
        result = verify_signal(
            self._signal("long"),
            [
                OhlcCandle("2026-05-05T09:00:00+09:00", open=100, high=104, low=99, close=103),
                OhlcCandle("2026-05-05T09:01:00+09:00", open=103, high=106, low=101, close=105),
            ],
        )

        self.assertEqual(result.outcome, "tp")
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.hit_price, 105.0)
        self.assertEqual(result.hit_target_index, 1)

    def test_short_stop_loss_before_take_profit(self):
        result = verify_signal(
            self._signal("short"),
            [
                OhlcCandle(datetime(2026, 5, 5, tzinfo=timezone.utc), open=100, high=104, low=97, close=103),
                OhlcCandle(datetime(2026, 5, 5, 0, 1, tzinfo=timezone.utc), open=103, high=106, low=101, close=105),
            ],
        )

        self.assertEqual(result.outcome, "sl")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.hit_price, 105.0)

    def test_same_candle_long_stop_and_target_is_ambiguous(self):
        result = verify_signal(
            self._signal("long"),
            [OhlcCandle("c1", open=100, high=106, low=94, close=101)],
        )

        self.assertEqual(result.outcome, "ambiguous")
        self.assertEqual(result.status, "needs_review")
        self.assertTrue(result.requires_manual_review)
        self.assertEqual(result.hit_target_index, 1)

    def test_same_candle_short_stop_and_target_is_ambiguous(self):
        result = verify_signal(
            self._signal("short"),
            [OhlcCandle("c1", open=100, high=106, low=94, close=101)],
        )

        self.assertEqual(result.outcome, "ambiguous")
        self.assertEqual(result.status, "needs_review")
        self.assertTrue(result.requires_manual_review)

    def test_returns_pending_when_no_level_hit(self):
        result = verify_signal(
            self._signal("long"),
            [OhlcCandle("c1", open=100, high=104, low=96, close=101)],
        )

        self.assertEqual(result.outcome, "no_hit")
        self.assertEqual(result.status, "pending")


if __name__ == "__main__":
    unittest.main()
