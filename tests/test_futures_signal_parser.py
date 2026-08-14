from datetime import datetime, timezone
import unittest

from src.futures_signals import FuturesSignalParseError, normalize_direction, normalize_symbol, parse_futures_signal


class FuturesSignalParserTests(unittest.TestCase):
    def test_parse_common_telegram_signal(self):
        signal = parse_futures_signal(
            """
            #MNQ M26 LONG
            Entry: 18750.25
            SL 18720
            TP1 18800
            TP2 18855.5
            """,
            source_message_id="42",
            received_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(signal.id, "telegram:42")
        self.assertEqual(signal.symbol, "MNQM26")
        self.assertEqual(signal.direction, "long")
        self.assertEqual(signal.entry, 18750.25)
        self.assertEqual(signal.stop_loss, 18720.0)
        self.assertEqual(signal.take_profits, (18800.0, 18855.5))
        self.assertEqual(signal.received_at, datetime(2026, 5, 5, tzinfo=timezone.utc))

    def test_parse_short_signal_and_normalize_month_name(self):
        signal = parse_futures_signal("/NQ SEP26 sell @ 18500 stop 18550 target 18420", source_message_id="99")

        self.assertEqual(signal.symbol, "NQU26")
        self.assertEqual(signal.direction, "short")
        self.assertEqual(signal.entry, 18500.0)
        self.assertEqual(signal.stop_loss, 18550.0)
        self.assertEqual(signal.take_profits, (18420.0,))

    def test_parse_korean_liquidation_keywords_as_take_profit(self):
        signal = parse_futures_signal(
            "나스닥(NQM26) 매수 진입 18800 손절 18750 1차청산 18840 부분청산 18880",
            source_message_id="kr-1",
        )

        self.assertEqual(signal.symbol, "NQM26")
        self.assertEqual(signal.direction, "long")
        self.assertEqual(signal.entry, 18800.0)
        self.assertEqual(signal.stop_loss, 18750.0)
        self.assertEqual(signal.take_profits, (18840.0, 18880.0))

    def test_parse_korean_market_entry_as_needs_review(self):
        signal = parse_futures_signal(
            "종목 : 나스닥(NQM26)\n포지션 : 매도(Short)\n진입가 : 시장가",
            source_message_id="market-1",
        )

        self.assertEqual(signal.symbol, "NQM26")
        self.assertEqual(signal.direction, "short")
        self.assertEqual(signal.entry, 0.0)
        self.assertEqual(signal.stop_loss, 0.0)
        self.assertEqual(signal.take_profits, ())
        self.assertEqual(signal.status, "needs_review")

    def test_normalizers(self):
        self.assertEqual(normalize_direction("BUY"), "long")
        self.assertEqual(normalize_direction("sell"), "short")
        self.assertEqual(normalize_symbol("/mes-j-2026"), "MESJ26")
        self.assertEqual(normalize_symbol("MNQ"), "MNQ")

    def test_rejects_invalid_price_shape(self):
        with self.assertRaises(FuturesSignalParseError):
            parse_futures_signal("MNQ long entry 100 SL 105 TP 110")

        with self.assertRaises(FuturesSignalParseError):
            parse_futures_signal("MNQ short entry 100 SL 95 TP 90")


if __name__ == "__main__":
    unittest.main()
