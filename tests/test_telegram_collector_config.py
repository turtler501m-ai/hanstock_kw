import json
import tempfile
import unittest
from pathlib import Path

from src.futures_signals import channels_from_legacy_json


class TelegramCollectorConfigTests(unittest.TestCase):
    def test_reads_signal_tracking_channels_from_legacy_channels_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "channels.json"
            path.write_text(
                json.dumps([
                    {"key": "nq", "id_or_username": "@nq_signals", "label": "NQ", "signal_tracking": True},
                    {"key": "chat", "id_or_username": "@general_chat", "label": "Chat", "signal_tracking": False},
                    {"key": "gold", "id_or_username": "-1001234567890", "label": "Gold", "signal_tracking": True},
                ]),
                encoding="utf-8",
            )

            self.assertEqual(channels_from_legacy_json(path), ("@nq_signals", "-1001234567890"))


if __name__ == "__main__":
    unittest.main()
