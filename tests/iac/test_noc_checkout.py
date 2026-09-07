"""Exercise production checkout tasks with local Git repositories only."""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible-playbook unavailable")
class NocCheckoutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="noc-checkout-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.checkout = self.root / "checkout"
        self.git("init", "-q", str(self.source))
        (self.source / "version").write_text("old")
        self.git("-C", str(self.source), "add", "version")
        self.commit("old")
        self.old = self.git("-C", str(self.source), "rev-parse", "HEAD")
        self.git("clone", "-q", str(self.source), str(self.checkout))
        (self.source / "version").write_text("new")
        self.git("-C", str(self.source), "add", "version")
        self.commit("new")
        self.new = self.git("-C", str(self.source), "rev-parse", "HEAD")
        tasks = yaml.safe_load((REPO / "ansible/roles/noc_agent/tasks/main.yml").read_text())
        names = {"Clone noc-agent repository", "Read deployed noc-agent revision",
                 "Require pinned noc-agent revision before continuing deployment"}
        self.tasks = [copy.deepcopy(task) for task in tasks if task.get("name") in names]
        self.assertEqual(len(self.tasks), 3)
        for task in self.tasks:
            task.pop("become", None)
            task.pop("become_user", None)
            task.pop("notify", None)
            if "retries" in task:
                task["retries"] = 1
                task["delay"] = 0

    def git(self, *args):
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git("-C", str(self.source), "-c", "user.name=Checkout test", "-c",
                 "user.email=checkout@example.invalid", "commit", "-qm", message)

    def run_tasks(self, *, source=None, omit_checkout=False, pin=None):
        marker = self.root / "continued"
        tasks = self.tasks[1:] if omit_checkout else self.tasks
        play = [{"hosts": "localhost", "connection": "local", "gather_facts": False,
                 "vars": {"noc_agent_repo": str(source or self.source),
                          "noc_agent_install_dir": str(self.checkout), "noc_agent_version": pin or self.new},
                 "tasks": tasks + [{"name": "Mark dependent work", "copy": {"content": "done", "dest": str(marker)}}]}]
        path = self.root / "play.yml"
        path.write_text(yaml.safe_dump(play))
        config = self.root / "ansible.cfg"
        config.write_text("[defaults]\nretry_files_enabled = False\n")
        env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCAL_TEMP=str(self.root / "local"), ANSIBLE_REMOTE_TEMP=str(self.root / "remote"))
        result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(path)],
                                env=env, capture_output=True, text=True, timeout=90)
        return result, marker.exists()

    def test_successful_checkout_matches_pin_before_dependent_work(self):
        result, continued = self.run_tasks()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(continued)
        self.assertEqual(self.git("-C", str(self.checkout), "rev-parse", "HEAD"), self.new)

    def test_failed_fetch_cannot_pass_using_existing_checkout(self):
        result, continued = self.run_tasks(source=self.root / "missing-repository")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(continued)
        self.assertEqual(self.git("-C", str(self.checkout), "rev-parse", "HEAD"), self.old)

    def test_mismatched_live_revision_stops_dependent_work(self):
        result, continued = self.run_tasks(omit_checkout=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(continued)
        self.assertIn("does not match its approved production SHA", result.stdout)

    def test_uppercase_pin_is_validated_and_matches_same_commit(self):
        self.git("-C", str(self.checkout), "fetch", "origin")
        self.git("-C", str(self.checkout), "checkout", "-q", self.new)
        result, continued = self.run_tasks(omit_checkout=True, pin=self.new.upper())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(continued)

    def test_uppercase_pin_cannot_skip_mismatch_validation(self):
        result, continued = self.run_tasks(omit_checkout=True, pin=self.new.upper())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(continued)
        self.assertIn("does not match its approved production SHA", result.stdout)
