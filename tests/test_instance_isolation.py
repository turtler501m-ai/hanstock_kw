import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "verify-instance-isolation.py"
SPEC = importlib.util.spec_from_file_location("verify_instance_isolation", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class InstanceIsolationTests(unittest.TestCase):
    def test_relative_database_paths_resolve_inside_instance_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "TRADE_DB_PATH=.runtime/trades.sqlite\n"
                "MISTOCK_TRADE_DB_PATH=.runtime/mistock/trades.sqlite\n",
                encoding="utf-8",
            )
            resolved = MODULE.verify(root)

        self.assertNotEqual(
            resolved["TRADE_DB_PATH"], resolved["MISTOCK_TRADE_DB_PATH"]
        )

    def test_database_path_outside_instance_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "TRADE_DB_PATH=../shared.sqlite\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                MODULE.verify(root)


if __name__ == "__main__":
    unittest.main()
