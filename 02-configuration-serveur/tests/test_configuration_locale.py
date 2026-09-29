import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "configuration-locale.sh"
MOCKS = ("sudo", "apt-get", "dpkg-query", "systemctl", "chronyc", "timedatectl",
         "locale-gen", "locale", "localectl", "date", "sleep")


class LocaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for command in MOCKS:
            (self.bin / command).symlink_to(HERE / "mock_command.py")
        self.state_file = self.root / "state.json"
        self.state = {"chrony": True, "chrony_active": True}

    def run_script(self, **environment):
        self.state_file.write_text(json.dumps(self.state))
        env = os.environ.copy()
        env.update(PATH=f"{self.bin}:{env['PATH']}", NTP_TEST_STATE=str(self.state_file),
                   NTP_WAIT_SECONDS="2", NTP_POLL_SECONDS="1")
        env.update(environment)
        result = subprocess.run(["bash", str(SCRIPT)], env=env, text=True,
                                capture_output=True, check=False)
        self.state = json.loads(self.state_file.read_text())
        return result

    def test_chrony_present_active_and_ubuntu_2604(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Service de temps : chrony", result.stdout)
        self.assertIn("Synchronisation confirmée : oui", result.stdout)
        self.assertFalse(any(c[0] == "apt-get" and "systemd-timesyncd" in c for c in self.state["calls"]))
        self.assertFalse(any(c[0] == "timedatectl" and "set-ntp" in c for c in self.state["calls"]))

    def test_chrony_stopped(self):
        self.state["chrony_active"] = False
        self.assertEqual(self.run_script().returncode, 0)
        self.assertTrue(self.state["chrony_active"])

    def test_timesyncd_alone(self):
        self.state = {"systemd-timesyncd": True}
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Service de temps : systemd-timesyncd", result.stdout)
        self.assertFalse(self.state.get("chrony", False))

    def test_timesyncd_delayed_sync(self):
        self.state = {"systemd-timesyncd": True, "sync_after": 2}
        self.assertIn("Synchronisation confirmée : oui", self.run_script().stdout)

    def test_timesyncd_timeout_only_warns(self):
        self.state = {"systemd-timesyncd": True, "sync_after": 99}
        result = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertIn("Synchronisation confirmée : non", result.stdout)

    def test_timesyncd_start_failure_is_fatal(self):
        self.state = {"systemd-timesyncd": True, "start_fails": True}
        self.assertNotEqual(self.run_script().returncode, 0)

    def test_no_backend_installs_chrony(self):
        self.state = {}
        self.assertEqual(self.run_script().returncode, 0)
        self.assertTrue(self.state["chrony"])

    def test_both_installed_disables_timesyncd_and_is_idempotent(self):
        self.state.update({"systemd-timesyncd": True,
                           "systemd-timesyncd_active": True,
                           "systemd-timesyncd_enabled": True})
        self.assertEqual(self.run_script().returncode, 0)
        self.assertFalse(self.state["systemd-timesyncd_active"])
        self.state["calls"] = []
        self.assertEqual(self.run_script().returncode, 0)
        self.assertFalse(any(c[0] == "systemctl" and c[1] == "disable" for c in self.state["calls"]))
        self.assertFalse(any(c[0] == "apt-get" and "chrony" in c for c in self.state["calls"]))

    def test_delayed_sync(self):
        self.state["sync_after"] = 2
        self.assertIn("Synchronisation confirmée : oui", self.run_script().stdout)

    def test_sync_timeout_only_warns(self):
        self.state["sync_after"] = 99
        result = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertIn("Avertissement", result.stderr)
        self.assertIn("Synchronisation confirmée : non", result.stdout)

    def test_start_failure_is_fatal_and_reports_command(self):
        self.state["start_fails"] = True
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("code retour", result.stderr)
        self.assertIn("systemctl enable", result.stderr)

    def test_zero_wait_never_calls_waitsync_zero(self):
        self.state["sync_after"] = 99
        result = self.run_script(NTP_WAIT_SECONDS="0")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Avertissement", result.stderr)
        self.assertFalse(any(c[:3] == ["chronyc", "waitsync", "0"] for c in self.state["calls"]))

    def test_invalid_poll(self):
        self.assertNotEqual(self.run_script(NTP_POLL_SECONDS="0").returncode, 0)

    def test_invalid_locale(self):
        self.assertNotEqual(self.run_script(TARGET_LOCALE="xx_XX.UTF-8").returncode, 0)

    def test_invalid_timezone(self):
        self.assertNotEqual(self.run_script(TARGET_TIMEZONE="Mars/Phobos").returncode, 0)

    def test_apt_cannot_remove_chrony_for_locale(self):
        self.state["apt_removes"] = "chrony"
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "apt-get" and "-s" not in c and "install" in c
                             for c in self.state["calls"]))

    def test_apt_cannot_remove_timesyncd_for_locale(self):
        self.state = {"systemd-timesyncd": True, "apt_removes": "systemd-timesyncd"}
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "apt-get" and "-s" not in c and "install" in c
                             for c in self.state["calls"]))


if __name__ == "__main__":
    unittest.main()
