import unittest

from src.config import config, settings_snapshot, temporary_settings


class RuntimeSettingsTests(unittest.TestCase):
    def test_snapshot_is_isolated_from_compatibility_singleton(self):
        snapshot = settings_snapshot()
        original = config.ai_score_weight
        try:
            config.ai_score_weight = original + 0.1
            self.assertEqual(snapshot.ai_score_weight, original)
        finally:
            config.ai_score_weight = original

    def test_temporary_settings_restores_values_after_failure(self):
        original = config.openai_model
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with temporary_settings(openai_model="temporary-model"):
                self.assertEqual(config.openai_model, "temporary-model")
                raise RuntimeError("stop")
        self.assertEqual(config.openai_model, original)

    def test_temporary_settings_rejects_unknown_fields(self):
        with self.assertRaises(KeyError):
            with temporary_settings(not_a_setting=True):
                pass


if __name__ == "__main__":
    unittest.main()
