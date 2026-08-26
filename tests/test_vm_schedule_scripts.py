import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VmScheduleScriptTests(unittest.TestCase):
    def test_domestic_jobs_share_cross_process_lock(self):
        daily = (ROOT / "scripts" / "vm" / "daily-auto.sh").read_text(
            encoding="utf-8"
        )
        dispatch = (ROOT / "scripts" / "vm" / "strategy-dispatch.sh").read_text(
            encoding="utf-8"
        )

        lock_assignment = 'LOCK_FILE="$RUNTIME_DIR/domestic-scheduler.lock"'
        self.assertIn(lock_assignment, daily)
        self.assertIn(lock_assignment, dispatch)
        self.assertIn("flock -n 9", daily)
        self.assertIn("flock -n 9", dispatch)

    def test_domestic_jobs_record_elapsed_time(self):
        daily = (ROOT / "scripts" / "vm" / "daily-auto.sh").read_text(encoding="utf-8")
        dispatch = (ROOT / "scripts" / "vm" / "strategy-dispatch.sh").read_text(encoding="utf-8")

        self.assertIn("duration_seconds=", daily)
        self.assertIn("duration_seconds=", dispatch)

    def test_cron_defaults_avoid_observed_runtime_overlap(self):
        daily_installer = (ROOT / "scripts" / "vm" / "install-daily-auto-cron.sh").read_text(encoding="utf-8")
        dispatch_installer = (ROOT / "scripts" / "vm" / "install-strategy-dispatch-cron.sh").read_text(encoding="utf-8")

        self.assertIn('TIME_SPEC="${1:-0 9,15 * * 1-5}"', daily_installer)
        self.assertIn('TIME_SPEC="${1:-7-57/10 9-15 * * 1-5}"', dispatch_installer)

    def test_cron_jobs_do_not_depend_on_executable_git_mode(self):
        installers = [
            "install-daily-auto-cron.sh",
            "install-strategy-dispatch-cron.sh",
            "install-plunge-bounce-cron.sh",
            "install-market-regime-preflight-cron.sh",
        ]
        for name in installers:
            content = (ROOT / "scripts" / "vm" / name).read_text(encoding="utf-8")
            self.assertIn("&& bash $ROOT_DIR/scripts/vm/", content, name)

    def test_kw_market_regime_preflight_schedule_is_read_only_and_isolated(self):
        runner = (ROOT / "scripts" / "vm" / "market-regime-preflight.sh").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "vm" / "install-market-regime-preflight-cron.sh").read_text(encoding="utf-8")
        updater = (ROOT / "scripts" / "vm" / "update.sh").read_text(encoding="utf-8")

        self.assertIn('TIME_SPEC="${1:-43 8 * * 1-5}"', installer)
        self.assertIn('CRON_TZ_VALUE="${HANSTOCK_CRON_TZ:-Asia/Seoul}"', installer)
        self.assertIn("hanstock-kw-market-regime-preflight", installer)
        self.assertNotIn("hanstock_ora", installer)
        self.assertIn("skip = 1", installer)
        self.assertIn("skip = 0", installer)
        self.assertIn("-m src.market_regime preflight --market KR", runner)
        self.assertNotIn("src.market_regime.cli", runner)
        self.assertIn('LOCK_FILE="$RUNTIME_DIR/market-regime-preflight.lock"', runner)
        self.assertNotIn("domestic-scheduler.lock", runner)
        self.assertNotIn("src.scheduler", runner)
        self.assertNotIn("daily_auto", runner)
        self.assertNotIn("strategy_dispatch", runner)
        self.assertIn("install-market-regime-preflight-cron.sh", updater)


if __name__ == "__main__":
    unittest.main()
