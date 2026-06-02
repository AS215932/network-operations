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

    def test_pull_request_jobs_use_the_unprivileged_runner(self):
        # Two-runner model (Wave 4): every job reachable on a `pull_request`
        # event must run on the unprivileged ci-pr runner (hyrule-public-pr),
        # never on a privileged self-hosted label (hyrule / hyrule-infra). The
        # heavy labs (batfish, containerlab-frr) may keep the privileged label
        # ONLY because they are if-gated off pull_request (workflow_dispatch /
        # repo var). Privileged deploy workflows (apply, drift-detection) are not
        # pull_request-triggered, so they legitimately stay on hyrule-infra.
        privileged = {"hyrule", "hyrule-infra"}
        for workflow in (REPO / ".github/workflows").glob("*.yml"):
            spec = yaml.safe_load(workflow.read_text())
            triggers = spec.get("on", spec.get(True))  # PyYAML maps `on:` -> True
            if not _triggers_on_pull_request(triggers):
                continue
            for job_name, job in (spec.get("jobs") or {}).items():
                runs_on = job.get("runs-on")
                labels = set(runs_on) if isinstance(runs_on, list) else {runs_on}
                offending = labels & privileged
                if not offending:
                    continue
                cond = str(job.get("if", ""))
                self.assertTrue(
                    "workflow_dispatch" in cond or "vars." in cond,
                    f"{workflow.name}:{job_name} uses privileged label {offending} on a "
                    f"pull_request workflow without an if-gate restricting it off PRs",
                )

    def test_privileged_deploy_workflows_stay_on_ci_runner(self):
        # apply/drift must keep the privileged runner and must NOT leak onto the
        # unprivileged ci-pr runner (they carry Vault + id_ci).
        for name in ("apply.yml", "drift-detection.yml"):
            text = (REPO / ".github/workflows" / name).read_text()
            self.assertIn("hyrule-infra", text, name)
            self.assertNotIn("hyrule-public-pr", text, name)

    def test_apply_workflow_can_gate_ci_runner_key_bootstrap(self):
        workflow = (REPO / ".github/workflows/apply.yml").read_text()

        self.assertIn("- ci-runner-key", workflow)
        self.assertIn("bootstrap_ci_runner_key:", workflow)
        self.assertIn("Connect as inventory users for first-time ci-runner-key bootstrap", workflow)
        self.assertIn("CI_KEY_PATH: /var/lib/github-runner/.ssh/id_ci", workflow)
        self.assertIn('apply_var="${playbook//-/_}_apply=true"', workflow)
        self.assertIn('user_args=(-e ansible_user=ci)', workflow)
        self.assertIn(
            'if [ "${playbook}" = "ci-runner-key" ] && [ "${{ inputs.bootstrap_ci_runner_key }}" = "true" ]; then',
            workflow,
        )
        self.assertIn('user_args=()', workflow)
        self.assertNotIn('${{ inputs.playbook }}_apply=true', workflow)

    def test_freebsd_playbooks_can_opt_into_become(self):
        freebsd_vars = yaml.safe_load((REPO / "ansible/inventory/group_vars/freebsd.yml").read_text())

        self.assertNotIn("ansible_become", freebsd_vars)
        self.assertEqual(freebsd_vars["ansible_become_method"], "doas")

    def test_ci_runner_deploy_user_uses_portable_shell(self):
        defaults = yaml.safe_load((REPO / "ansible/roles/ci_runner_key/defaults/main.yml").read_text())

        self.assertEqual(defaults["ci_runner_user_shell"], "/bin/sh")

    def test_freebsd_router_inventory_uses_loopback_addresses(self):
        inventory = yaml.safe_load((REPO / "ansible/inventory/hosts.yml").read_text())
        freebsd_hosts = inventory["all"]["children"]["freebsd"]["hosts"]

        self.assertEqual(freebsd_hosts["cr1-nl1"]["ansible_host"], "2a0c:b641:b50::a")
        self.assertEqual(freebsd_hosts["cr1-de1"]["ansible_host"], "2a0c:b641:b50::b")

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


def _triggers_on_pull_request(triggers):
    # `on:` may be a string ("pull_request"), a list, or a mapping
    # ({pull_request: {...}, push: {...}}); PyYAML also turns the bare key `on`
    # into the boolean True, which the caller resolves before passing here.
    if triggers is None:
        return False
    if isinstance(triggers, str):
        return triggers == "pull_request"
    if isinstance(triggers, dict):
        return "pull_request" in triggers
    if isinstance(triggers, (list, tuple, set)):
        return "pull_request" in triggers
    return False


if __name__ == "__main__":
    unittest.main()
