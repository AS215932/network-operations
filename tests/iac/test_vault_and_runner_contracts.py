import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
JOURNAL_GROUP = "systemd-journal"


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

    def test_apply_workflow_can_gate_ci_runner_key_bootstrap(self):
        workflow = (REPO / ".github/workflows/apply.yml").read_text()

        self.assertIn("- ci-runner-key", workflow)
        self.assertIn("CI_KEY_PATH: /var/lib/github-runner/.ssh/id_ci", workflow)
        self.assertIn('apply_var="${playbook//-/_}_apply=true"', workflow)
        self.assertNotIn('${{ inputs.playbook }}_apply=true', workflow)

    def test_runner_known_hosts_is_seeded_without_controller_key_path(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/github_runner/tasks/main.yml").read_text())

        seed_task = _task_by_name(tasks, "Seed runner known_hosts with the infra fleet host keys")
        self.assertIsNotNone(seed_task)
        self.assertNotIn("when", seed_task)

        ownership_task = _task_by_name(tasks, "Fix runner known_hosts ownership")
        self.assertIsNotNone(ownership_task)
        self.assertNotIn("when", ownership_task)

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
        hyrule_mcp_tasks = yaml.safe_load((REPO / "ansible/roles/hyrule_mcp/tasks/main.yml").read_text())
        noc_mcp_key_tasks = yaml.safe_load((REPO / "ansible/roles/noc_mcp_key/tasks/main.yml").read_text())

        self.assertIn(f"SupplementaryGroups={JOURNAL_GROUP}", service)
        self.assertEqual(
            _task_by_name(hyrule_mcp_tasks, "Ensure systemd journal reader group exists")["group"]["name"],
            JOURNAL_GROUP,
        )
        self.assertIn(
            JOURNAL_GROUP,
            _groups_for(_task_by_name(hyrule_mcp_tasks, "Ensure noc-agent system user exists")["user"]["groups"]),
        )
        self.assertEqual(
            _task_by_name(noc_mcp_key_tasks, "Ensure systemd journal reader group exists")["group"]["name"],
            JOURNAL_GROUP,
        )
        self.assertIn(
            JOURNAL_GROUP,
            _groups_for(
                _task_by_name(noc_mcp_key_tasks, "Grant MCP SSH user read access to systemd journals")["user"]["groups"]
            ),
        )


def _task_by_name(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(f"task not found: {name}")


def _groups_for(value):
    if isinstance(value, list):
        return {str(item) for item in value}
    return {part.strip() for part in str(value).replace(",", " ").split() if part.strip()}


if __name__ == "__main__":
    unittest.main()
