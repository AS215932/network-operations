"""Contract tests for the FRR role's reload-command fallback (PR #585 finding).

The OpenBSD FRR package may not ship the `frr-reload` delta wrapper the
FreeBSD port and Debian package provide. The role must probe for the primary
command at apply time and fall back (OpenBSD: `rcctl reload frr`) instead of
failing into the at(1) watchdog and flapping BGP sessions.
"""
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
DEPLOY_TASKS = REPO / "ansible/roles/frr/tasks/deploy.yml"
VARS_DIR = REPO / "ansible/roles/frr/vars"


def _tasks():
    return yaml.safe_load(DEPLOY_TASKS.read_text())


def _task_by_name(name):
    return {task.get("name"): task for task in _tasks()}


class FrrReloadFallbackTest(unittest.TestCase):
    def test_reload_probe_runs_before_staging(self):
        tasks = _tasks()
        names = [task.get("name") for task in tasks]
        probe = "Resolve effective reload command (fall back when the primary wrapper is absent)"
        self.assertIn(probe, names)
        # The probe must resolve the command before anything that uses it
        # (watchdog schedule, swap, reload).
        self.assertLess(names.index(probe), names.index("Stage new FRR config on host (.new)"))
        self.assertLess(
            names.index(probe),
            names.index("Schedule rollback watchdog — restores + reloads the backup if not cancelled"),
        )

    def test_probe_never_fails_the_play(self):
        probe = _task_by_name(
            "Resolve effective reload command (fall back when the primary wrapper is absent)"
        )["Resolve effective reload command (fall back when the primary wrapper is absent)"]
        # A missing primary wrapper is the expected OpenBSD case, not an error.
        self.assertFalse(probe.get("failed_when", True))

    def test_watchdog_and_reload_use_the_effective_command(self):
        by_name = _task_by_name("x")
        watchdog = by_name[
            "Schedule rollback watchdog — restores + reloads the backup if not cancelled"
        ]
        reload = by_name["Reload FRR (integrated delta apply — converges running daemon, no restart)"]
        self.assertIn("_frr_effective_reload_cmd", watchdog["shell"])
        self.assertNotIn("{{ frr_reload_cmd }}", watchdog["shell"])
        self.assertEqual(reload["command"], "{{ _frr_effective_reload_cmd }}")

    def test_openbsd_defines_a_fallback_and_other_platforms_opt_out(self):
        openbsd = yaml.safe_load((VARS_DIR / "OpenBSD.yml").read_text())
        self.assertTrue(openbsd.get("frr_reload_fallback_cmd"), "OpenBSD needs a reload fallback")
        for platform in ("FreeBSD.yml", "Debian.yml"):
            vars_ = yaml.safe_load((VARS_DIR / platform).read_text())
            self.assertIn("frr_reload_fallback_cmd", vars_, f"{platform} must declare the fallback key")
            self.assertEqual(vars_["frr_reload_fallback_cmd"], "")


if __name__ == "__main__":
    unittest.main()
