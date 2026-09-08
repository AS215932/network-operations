"""Regression coverage for restart callbacks during a schema rollout."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "ansible/roles/hyrule_cloud/tasks"


class CloudQuiescenceTests(unittest.TestCase):
    def test_barrier_precedes_any_application_mutation(self):
        tasks = yaml.safe_load((TASKS / "apply.yml").read_text())
        imports = [t.get("ansible.builtin.import_tasks") for t in tasks]
        for later in ("checkout.yml", "runtime.yml", "vault.yml", "health.yml"):
            self.assertLess(imports.index("quiesce.yml"), imports.index(later))
        barrier = yaml.safe_load((TASKS / "quiesce.yml").read_text())
        kinds = [next(k for k in t if k.startswith("ansible.builtin.")) for t in barrier]
        self.assertLess(kinds.index("ansible.builtin.copy"), kinds.index("ansible.builtin.systemd"))
        stop = next(t for t in barrier if t.get("ansible.builtin.systemd", {}).get("state") == "stopped")
        preflight = next(t for t in barrier if "ansible.builtin.script" in t)
        self.assertLess(barrier.index(stop), barrier.index(preflight))
        self.assertNotIn("failed_when", stop)
        self.assertNotIn("failed_when", preflight)

    def test_worker_remains_held_until_api_is_healthy(self):
        tasks = yaml.safe_load((TASKS / "health.yml").read_text())
        names = [t["name"] for t in tasks]
        ordered = [
            "Require application checkout before releasing deployment barriers",
            "Recheck provisioning with the deployment runtime before migration",
            "Run hyrule-cloud database migrations",
            "Release API start barrier after successful migration",
            "Restart hyrule-cloud (deterministic on every apply)",
            "HTTP health check (local loopback)",
            "Release worker start barrier after API health succeeds",
            "Restart dedicated hyrule-cloud worker (deterministic on every apply)",
        ]
        self.assertEqual([names.index(n) for n in ordered], sorted(names.index(n) for n in ordered))
        for name in ordered:
            self.assertNotIn("ignore_errors", tasks[names.index(name)])

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd tools unavailable")
    def test_systemd_rejects_start_while_persistent_marker_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "deployment-pending"
            condition = "ConditionPathExists=!" + str(marker)
            def evaluate():
                return subprocess.run(["systemd-analyze", "condition", condition], capture_output=True).returncode
            self.assertEqual(evaluate(), 0)
            marker.write_text("pending")
            self.assertNotEqual(evaluate(), 0)
            # A second evaluation simulates a fresh manager reading durable state.
            self.assertNotEqual(evaluate(), 0)
            marker.unlink()
            self.assertEqual(evaluate(), 0)


class CloudSystemdIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').environ.get('AS215932_SYSTEMD_TEST') == '1',
                         'opt-in disposable user-systemd integration')
    def test_restart_callbacks_and_interrupted_retry(self):
        import uuid
        from jinja2 import Template

        def ctl(*args):
            result = subprocess.run(['systemctl', '--user', *args],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout.strip()

        source = yaml.safe_load((TASKS / 'quiesce.yml').read_text())[1]
        template = Template(source['ansible.builtin.copy']['content'])
        units = ['as215932-quiescence-' + uuid.uuid4().hex + '-' + kind
                 for kind in ('api', 'worker')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markers = [root / (unit + '.deployment-pending') for unit in units]
            receipts = [root / (unit + '.started') for unit in units]
            linked = []
            try:
                for unit, marker, receipt in zip(units, markers, receipts):
                    marker.write_text('deployment pending')
                    config = template.render(item=unit, hyrule_cloud_etc_dir=directory)
                    path = root / (unit + '.service')
                    path.write_text(config + '\n[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                                    'ExecStart=/usr/bin/touch ' + str(receipt) + '\n')
                    ctl('link', '--runtime', str(path))
                    linked.append(unit)
                ctl('daemon-reload')
                # First installation: dependencies or manual starts cannot run code.
                for unit in units:
                    ctl('start', unit)
                self.assertTrue(all(not r.exists() for r in receipts))
                # Interrupted migration: reload and restart callbacks preserve holds.
                ctl('daemon-reload')
                for unit in units:
                    ctl('restart', unit)
                    ctl('try-restart', unit)
                self.assertTrue(all(not r.exists() for r in receipts))
                # Successful migration releases API only; failed API health would
                # leave the worker marker untouched through any callback.
                markers[0].unlink()
                ctl('restart', units[0])
                ctl('restart', units[1])
                self.assertTrue(receipts[0].exists())
                self.assertFalse(receipts[1].exists())
                # Retry re-establishes both holds before changing checkout.
                for marker in markers:
                    marker.write_text('retry pending')
                for unit in reversed(units):
                    ctl('stop', unit)
                receipts[0].unlink()
                for unit in units:
                    ctl('restart', unit)
                self.assertTrue(all(not r.exists() for r in receipts))
                # After migration and API health, the worker can finally run.
                markers[0].unlink()
                ctl('restart', units[0])
                self.assertTrue(receipts[0].exists())
                markers[1].unlink()
                ctl('restart', units[1])
                self.assertTrue(receipts[1].exists())
            finally:
                for unit in reversed(linked):
                    subprocess.run(['systemctl', '--user', 'stop', unit],
                                   capture_output=True, timeout=30)
                    subprocess.run(['systemctl', '--user', 'disable', '--runtime', unit],
                                   capture_output=True, timeout=30)
                subprocess.run(['systemctl', '--user', 'daemon-reload'],
                               capture_output=True, timeout=30)
