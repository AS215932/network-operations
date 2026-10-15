import unittest
from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parents[2] / "ansible/roles/icinga2"


def _tasks():
    return yaml.safe_load((ROLE / "tasks/install.yml").read_text())


def _dest(task):
    body = task.get("template") or task.get("file") or {}
    return str(body.get("dest") or body.get("path") or "")


class IcingaApiUsersTest(unittest.TestCase):
    def test_each_api_user_has_its_own_template(self):
        noc = (ROLE / "templates/api-users-noc-agent.conf.j2").read_text()
        ops = (ROLE / "templates/api-users-ops-readonly.conf.j2").read_text()
        self.assertIn('object ApiUser "noc-agent"', noc)
        self.assertNotIn("ops-readonly", noc.split("object ApiUser", 1)[1])
        self.assertIn('object ApiUser "ops-readonly"', ops)
        self.assertNotIn('ApiUser "noc-agent"', ops)
        self.assertNotIn("actions/", ops)

    def test_user_files_are_gated_only_on_their_own_secret(self):
        tasks = {_dest(t): t for t in _tasks() if "template" in t}
        noc = tasks["{{ icinga2_conf_dir }}/api-users-noc-agent.conf"]
        ops = tasks["{{ icinga2_conf_dir }}/api-users-ops-readonly.conf"]
        self.assertIn("icinga2_noc_agent_api_password", noc["when"])
        self.assertNotIn("ops_readonly", noc["when"])
        self.assertIn("icinga2_ops_readonly_api_password", ops["when"])
        self.assertNotIn("noc_agent", ops["when"])

    def test_no_task_removes_another_users_file(self):
        for task in _tasks():
            body = task.get("file")
            if not body or body.get("state") != "absent":
                continue
            path = str(body.get("path"))
            self.assertNotIn("api-users-noc-agent", path)
            self.assertNotIn("api-users-ops-readonly", path)
            if "api-users-managed" in path:
                # Legacy cleanup only when the noc-agent user is re-homed.
                self.assertIn("icinga2_noc_agent_api_password", task["when"])
                self.assertNotIn("ops_readonly", task["when"])

    def test_legacy_file_removed_only_after_noc_agent_file_written(self):
        names = [_dest(t) for t in _tasks()]
        self.assertLess(
            names.index("{{ icinga2_conf_dir }}/api-users-noc-agent.conf"),
            names.index("{{ icinga2_conf_dir }}/api-users-managed.conf"),
        )


if __name__ == "__main__":
    unittest.main()
