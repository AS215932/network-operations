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

    def test_runner_unit_reaps_orphans_on_restart(self):
        # KillMode=process orphaned Runner.Listener on a mid-job restart: it held
        # the GitHub session (next start → SessionConflict crash-loop) and ran in a
        # torn-down PrivateTmp /tmp (mktemp failures). mixed reaps the whole cgroup.
        unit = (REPO / "ansible/roles/github_runner/templates/github-runner.service.j2").read_text()
        self.assertIn("KillMode=mixed", unit)
        self.assertNotIn("KillMode=process", unit)

    def test_runner_staging_unmount_removes_fstab_entry(self):
        # state: unmounted left the staging mountpoint in /etc/fstab, duplicating
        # the runner-home device entry and racing two mounts of /dev/xvdiN on boot.
        tasks = (REPO / "ansible/roles/github_runner/tasks/main.yml").read_text()
        self.assertNotRegex(tasks, re.compile(r"state:\s*unmounted"))

    def test_vault_agent_supports_response_wrapped_secret_id(self):
        hcl = (REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2").read_text()
        self.assertIn("secret_id_response_wrapping_path", hcl)
        self.assertIn("remove_secret_id_file_after_reading = true", hcl)


if __name__ == "__main__":
    unittest.main()
