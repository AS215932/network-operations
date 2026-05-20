import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class VaultAndRunnerContractsTest(unittest.TestCase):
    def test_runner_labels_cover_all_workflows(self):
        defaults = yaml.safe_load((REPO / "ansible/roles/github_runner/defaults/main.yml").read_text())
        labels = {str(label).replace("{{ github_runner_arch }}", defaults["github_runner_arch"]) for label in defaults["github_runner_labels"]}
        self.assertTrue({"self-hosted", "linux", "x64", "hyrule", "hyrule-infra"} <= labels)

    def test_infra_workflows_target_hyrule_infra_runner_label(self):
        for workflow in (REPO / ".github/workflows").glob("*.yml"):
            text = workflow.read_text()
            if "runs-on:" in text:
                self.assertIn("hyrule-infra", text, workflow)

    def test_hyrule_cloud_policy_is_dedicated(self):
        policy = (REPO / "configs/vault/policies/hyrule-cloud.hcl").read_text()
        self.assertIn('path "kv/data/hyrule-cloud"', policy)
        self.assertNotIn("kv/data/ci-runner", policy)
        self.assertNotIn("kv/data/noc-agent", policy)

    def test_cloud_role_no_longer_renders_secret_env_from_ansible(self):
        role_text = "\n".join(path.read_text() for path in (REPO / "ansible/roles/hyrule_cloud/tasks").glob("*.yml"))
        self.assertNotIn("configs/hyrule-cloud.env.j2", role_text)
        self.assertNotRegex(role_text, re.compile(r"lookup\(['\"]env['\"],\s*['\"]XO_TOKEN['\"]"))

    def test_vault_agent_supports_response_wrapped_secret_id(self):
        hcl = (REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2").read_text()
        self.assertIn("secret_id_response_wrapping_path", hcl)
        self.assertIn("remove_secret_id_file_after_reading = true", hcl)

    def test_hyrule_mcp_users_can_read_systemd_journals(self):
        service = (REPO / "configs/hyrule-mcp.service").read_text()
        hyrule_mcp_tasks = (REPO / "ansible/roles/hyrule_mcp/tasks/main.yml").read_text()
        noc_mcp_key_tasks = (REPO / "ansible/roles/noc_mcp_key/tasks/main.yml").read_text()

        self.assertIn("SupplementaryGroups=systemd-journal", service)
        self.assertRegex(hyrule_mcp_tasks, re.compile(r"groups:\s*systemd-journal"))
        self.assertRegex(noc_mcp_key_tasks, re.compile(r"groups:\s*systemd-journal"))


if __name__ == "__main__":
    unittest.main()
