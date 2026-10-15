import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


class NocAgentRetirementTest(unittest.TestCase):
    def test_noc_playbook_retires_noc_agent_and_keeps_mcp_servers(self):
        play = yaml.safe_load((REPO / "ansible/playbooks/noc.yml").read_text())[0]
        roles = [r if isinstance(r, str) else r["role"] for r in play["roles"]]
        self.assertNotIn("noc_agent", roles)
        self.assertIn("noc_agent_retire", roles)
        self.assertIn("hyrule_mcp", roles)
        self.assertIn("xo_mcp", roles)
        self.assertNotIn("post_tasks", play)
        self.assertNotIn("noc-agent.service", Path(REPO / "ansible/playbooks/noc.yml").read_text())

    def test_retire_role_stops_units_and_keeps_data(self):
        role = REPO / "ansible/roles/noc_agent_retire"
        units = yaml.safe_load((role / "defaults/main.yml").read_text())["noc_agent_retire_units"]
        self.assertEqual(units, ["noc-agent", "noc-agent-bot"])
        tasks = yaml.safe_load((role / "tasks/main.yml").read_text())
        stop = next(t for t in tasks if "systemd" in t)
        self.assertEqual(stop["systemd"]["state"], "stopped")
        self.assertFalse(stop["systemd"]["enabled"])
        for task in tasks:
            if "file" in task:
                self.assertTrue(task["file"]["path"].startswith("/etc/systemd/system/"))

    def test_noc_agent_is_not_promoted_or_pinned(self):
        self.assertFalse((REPO / "ansible/roles/noc_agent").exists())
        host_vars = yaml.safe_load((REPO / "ansible/inventory/host_vars/noc.yml").read_text())
        self.assertNotIn("noc_agent_version", host_vars)
        self.assertNotIn("noc_knowledge_version", host_vars)
        self.assertNotIn("AS215932/noc-agent", (REPO / ".github/workflows/promote-apps.yml").read_text())
        self.assertNotIn("noc_agent_version", (REPO / "scripts/ci/promote-app-pins.py").read_text())


if __name__ == "__main__":
    unittest.main()
