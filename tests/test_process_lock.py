import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.utils.process_lock import ProcessLock


class ProcessLockTests(unittest.TestCase):
    def test_second_owner_is_rejected_until_release(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            first = ProcessLock("scheduler", lock_dir=tmpdir)
            second = ProcessLock("scheduler", lock_dir=tmpdir)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_stale_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            path = Path(tmpdir) / "scheduler.lock"
            path.write_text("999999999:stale", encoding="ascii")

            lock = ProcessLock("scheduler", lock_dir=tmpdir)
            self.assertTrue(lock.acquire())
            self.assertTrue(path.read_text(encoding="ascii").startswith(f"{os.getpid()}:"))
            lock.release()
            self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows-specific PID query")
    def test_windows_owner_check_does_not_call_os_kill(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            first = ProcessLock("scheduler", lock_dir=tmpdir)
            second = ProcessLock("scheduler", lock_dir=tmpdir)
            self.assertTrue(first.acquire())
            with patch("src.utils.process_lock.os.kill", side_effect=AssertionError("os.kill called")):
                self.assertFalse(second.acquire())
            first.release()


if __name__ == "__main__":
    unittest.main()
