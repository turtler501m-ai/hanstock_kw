import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VmServiceSafetyTest(unittest.TestCase):
    def test_vm_units_use_hanstock_kw_repo_path(self):
        expected_root = "/home/ubuntu/hanstock_kw"
        for unit_name in (
            "hanstock-kw.service",
            "hanstock-kw-condition-monitor.service",
        ):
            unit = (ROOT / "scripts/vm" / unit_name).read_text(encoding="utf-8")
            self.assertIn(f"WorkingDirectory={expected_root}", unit)
            self.assertIn(f"EnvironmentFile={expected_root}/.env", unit)
            self.assertNotIn("/home/ubuntu/hanstock/", unit)
            self.assertNotIn("StandardOutput=append:", unit)
            self.assertNotIn("StandardError=append:", unit)

    def test_deploy_defaults_target_hanstock_kw_repository(self):
        deploy_script = (ROOT / "scripts/local/deploy-vm.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('else { "~/hanstock_kw" }', deploy_script)
        self.assertIn(
            'else { "https://github.com/turtler501m-ai/hanstock_kw.git" }',
            deploy_script,
        )
        self.assertNotIn("hanstock_ora.git", deploy_script)
        self.assertIn('else { "~/hanstock/.env" }', deploy_script)
        self.assertIn('chmod 600 "$REPO_PATH/.env"', deploy_script)
        self.assertIn('bash ./scripts/vm/update.sh "$BRANCH"', deploy_script)

    def test_vm_update_uses_checked_in_deploy_constraints(self):
        update_script = (ROOT / "scripts/vm/update.sh").read_text(encoding="utf-8")

        self.assertIn("--constraint constraints-deploy.txt", update_script)
        self.assertIn("--requirement requirements-core.txt", update_script)
        self.assertIn("--requirement requirements-integrations.txt", update_script)
        self.assertNotIn("constraints/vm-python.lock", update_script)
        self.assertIn('bash "$ROOT_DIR/scripts/vm/server.sh" restart', update_script)
        self.assertIn('mkdir -p "$ROOT_DIR/logs" "$ROOT_DIR/.runtime"', update_script)
        self.assertNotIn('scripts/vm/hanstock-autonomy.service', update_script)
        self.assertIn('scripts/vm/hanstock-kw-condition-monitor.service', update_script)
        self.assertNotIn(
            '/etc/systemd/system/hanstock-condition-monitor.service', update_script
        )

    def test_dashboard_systemd_listens_on_public_interface(self):
        server_script = (ROOT / "scripts/vm/server.sh").read_text(encoding="utf-8")
        systemd_unit = (ROOT / "scripts/vm/hanstock-kw.service").read_text(
            encoding="utf-8"
        )

        self.assertIn('HOST="${HOST:-127.0.0.1}"', server_script)
        self.assertIn("--host 0.0.0.0", systemd_unit)
        self.assertIn("--port 8001", systemd_unit)

    def test_deploy_syncs_systemd_unit_before_restart(self):
        update_script = (ROOT / "scripts/vm/update.sh").read_text(encoding="utf-8")

        install_position = update_script.index(
            "/etc/systemd/system/hanstock-kw.service"
        )
        reload_position = update_script.index("systemctl daemon-reload")
        restart_position = update_script.index(
            '"$ROOT_DIR/scripts/vm/server.sh" restart'
        )
        self.assertLess(install_position, reload_position)
        self.assertLess(reload_position, restart_position)

    def test_deploy_verifies_database_isolation(self):
        update_script = (ROOT / "scripts/vm/update.sh").read_text(encoding="utf-8")
        self.assertIn("tools/verify-instance-isolation.py", update_script)

    def test_deploy_runs_post_restart_operations_smoke_check(self):
        update_script = (ROOT / "scripts/vm/update.sh").read_text(encoding="utf-8")
        restart_position = update_script.index('"$ROOT_DIR/scripts/vm/server.sh" restart')
        smoke_position = update_script.index("tools/deployment-smoke.py")
        self.assertLess(restart_position, smoke_position)
        self.assertIn("--base-url \"http://127.0.0.1:8001\"", update_script)

    def test_local_vm_dashboard_uses_loopback_tunnel(self):
        tunnel_script = (ROOT / "scripts/local/vm-dashboard.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            '"-L", "127.0.0.1:${LocalPort}:127.0.0.1:${RemotePort}"',
            tunnel_script,
        )
        self.assertIn("[int]$LocalPort = 18001", tunnel_script)
        self.assertIn("[int]$RemotePort = 8001", tunnel_script)

    def test_kiwoom_mistock_cron_has_separate_marker(self):
        installer = (ROOT / "scripts/vm/install-mistock-cron.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("# hanstock-kw-mistock-auto begin", installer)
        self.assertIn("# hanstock-kw-mistock-auto end", installer)
        self.assertIn("scripts/vm/mistock-auto.sh", installer)


if __name__ == "__main__":
    unittest.main()
