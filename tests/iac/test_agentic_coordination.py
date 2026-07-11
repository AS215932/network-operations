import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load_yaml(path: str) -> dict:
    return yaml.safe_load((REPO / path).read_text()) or {}


class AgenticCoordinationContractsTest(unittest.TestCase):
    def test_soc_vm_exists_but_runtime_ships_dark(self):
        inventory = load_yaml("ansible/inventory/hosts.yml")
        linux = inventory["all"]["children"]["linux"]["hosts"]
        infra = inventory["all"]["children"]["infra_vms"]["hosts"]
        peers = load_yaml("ansible/inventory/group_vars/all.yml")["peers"]
        host_vars = load_yaml("ansible/inventory/host_vars/soc.yml")

        self.assertEqual(linux["soc"]["ansible_host"], "2a0c:b641:b50:2::100")
        self.assertIn("soc", infra)
        self.assertEqual(peers["soc"]["ipv6"], "2a0c:b641:b50:2::100")
        self.assertEqual(host_vars["soc_agent_version"], "main")
        self.assertEqual(host_vars["soc_network_operations_version"], "main")
        self.assertEqual(host_vars["soc_mode"], "shadow")
        for key in (
            "soc_enabled",
            "soc_posture_enabled",
            "soc_posture_timer_enabled",
            "soc_agent_service_enabled",
            "soc_handoff_timer_enabled",
            "soc_probe_timer_enabled",
            "soc_coordinator_enabled",
            "soc_redteam_enabled",
            "soc_redteam_allow_active_probes",
            "soc_lhp_enabled",
            "soc_posture_handoff_enabled",
        ):
            self.assertFalse(host_vars[key], key)

    def test_soc_rollout_is_pinned_cumulative_and_senior_approved(self):
        validation = (REPO / "ansible/roles/soc_agent/tasks/main.yml").read_text()
        mode_env = (
            REPO / "ansible/roles/soc_agent/templates/soc-agent-mode.env.j2"
        ).read_text()
        probe_service = (
            REPO / "ansible/roles/soc_agent/templates/soc-probes.service.j2"
        ).read_text()

        for mode in (
            "shadow",
            "case_only",
            "handoff_dry",
            "handoff_live",
            "probe_dry",
            "probe_live",
        ):
            self.assertIn(f"'{mode}'", validation)
        self.assertIn("soc_agent_version is match('^[0-9a-f]{40}$')", validation)
        self.assertIn(
            "soc_network_operations_version is match('^[0-9a-f]{40}$')",
            validation,
        )
        self.assertIn(
            "['handoff_dry', 'handoff_live', 'probe_dry', 'probe_live']",
            validation,
        )
        self.assertIn("soc_redteam_max_tier | int == 2", validation)
        self.assertIn("SOC_REDTEAM_ALLOW_ACTIVE_PROBES", mode_env)
        self.assertIn("SOC_REDTEAM_MAX_TIER", mode_env)
        self.assertIn("socctl probes run-once", probe_service)
        self.assertNotIn("remediat", probe_service.lower())

    def test_central_coordinator_is_overlay_only_pinned_and_dark(self):
        defaults = load_yaml("ansible/roles/agent_core_coordinator/defaults/main.yml")
        host_vars = load_yaml("ansible/inventory/host_vars/loop.yml")
        validation = (
            REPO / "ansible/roles/agent_core_coordinator/tasks/main.yml"
        ).read_text()
        service = (
            REPO
            / "ansible/roles/agent_core_coordinator/templates/agent-core-coordinator.service.j2"
        ).read_text()

        self.assertFalse(defaults["agent_core_coordinator_apply"])
        self.assertFalse(defaults["agent_core_coordinator_enabled"])
        self.assertEqual(host_vars["agent_core_coordinator_version"], "main")
        self.assertFalse(host_vars["agent_core_coordinator_enabled"])
        self.assertEqual(host_vars["agent_core_coordinator_port"], 8771)
        self.assertIn("agent_core_coordinator_bind == peers.loop.ipv6", validation)
        self.assertIn(
            "agent_core_coordinator_version is match('^[0-9a-f]{40}$')",
            validation,
        )
        self.assertIn(
            "ExecStart={{ agent_core_coordinator_install_dir }}/.venv/bin/agent-core-coordinator",
            service,
        )
        self.assertIn("ProtectSystem=strict", service)

    def test_each_loop_has_a_signed_coordinator_adapter(self):
        loop_vars = load_yaml("ansible/inventory/host_vars/loop.yml")
        noc_vars = load_yaml("ansible/inventory/host_vars/noc.yml")
        soc_vars = load_yaml("ansible/inventory/host_vars/soc.yml")
        templates = {
            "engineering": "ansible/roles/vault_agent/templates/engineering-loop.env.ctmpl.j2",
            "knowledge": "ansible/roles/vault_agent/templates/knowledge-loop.env.ctmpl.j2",
            "noc": "ansible/roles/vault_agent/templates/noc-agent.env.ctmpl.j2",
            "soc": "ansible/roles/vault_agent/templates/soc-agent.env.ctmpl.j2",
        }

        self.assertFalse(loop_vars["engineering_loop_coordinator_enabled"])
        self.assertFalse(loop_vars["knowledge_loop_coordinator_enabled"])
        self.assertFalse(loop_vars["agentic_observatory_coordinator_enabled"])
        self.assertFalse(noc_vars["noc_coordinator_worker_enabled"])
        self.assertFalse(soc_vars["soc_coordinator_enabled"])
        for loop, rel_path in templates.items():
            with self.subTest(loop=loop):
                text = (REPO / rel_path).read_text()
                self.assertIn("HYRULE_COORDINATOR_SECRET", text)
                self.assertIn("HYRULE_COORDINATOR", text)

        self.assertIn(
            loop_vars["knowledge_loop_coordinator_proposal_dir"],
            loop_vars["knowledge_loop_learning_event_paths"],
        )

    def test_vault_scopes_do_not_expose_workload_secrets_to_runner(self):
        runner = (REPO / "configs/vault/policies/github-runner.hcl").read_text()
        coordinator = (
            REPO / "configs/vault/policies/agent-core-coordinator.hcl"
        ).read_text()
        soc = (REPO / "configs/vault/policies/soc-agent.hcl").read_text()

        for role in ("agent-core-coordinator", "soc-agent"):
            self.assertIn(f'path "auth/approle/role/{role}/role-id"', runner)
            self.assertIn(f'path "auth/approle/role/{role}/secret-id"', runner)
            self.assertNotIn(f'path "kv/data/{role}"', runner)
        self.assertIn('path "kv/data/agent-core-coordinator"', coordinator)
        self.assertIn('path "kv/data/soc-agent"', soc)
        self.assertNotIn("soc-agent", coordinator)
        self.assertNotIn("agent-core-coordinator", soc)

    def test_observatory_and_promotion_paths_cover_the_new_plane(self):
        observatory_env = (
            REPO
            / "ansible/roles/vault_agent/templates/agentic-observatory.env.ctmpl.j2"
        ).read_text()
        promotion = (REPO / ".github/workflows/promote-apps.yml").read_text()
        deployment = (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()

        for setting in (
            "OBSERVATORY_GITHUB_OAUTH_CLIENT_ID",
            "OBSERVATORY_GITHUB_OAUTH_CLIENT_SECRET",
            "OBSERVATORY_GITHUB_OAUTH_POLICY_TOKEN",
            "OBSERVATORY_GITHUB_OAUTH_ORG",
            "OBSERVATORY_GITHUB_OAUTH_REQUIRE_ORG_2FA",
            "OBSERVATORY_COORDINATOR_BASE_URL",
            "OBSERVATORY_COORDINATOR_SECRET",
        ):
            self.assertIn(setting, observatory_env)

        for repo in (
            "AS215932/agent-core)",
            "AS215932/soc-agent)",
            "AS215932/noc-agent)",
            "AS215932/engineering-loop)",
            "AS215932/knowledge)",
            "AS215932/agentic-observatory)",
        ):
            self.assertIn(repo, promotion)
        self.assertIn(
            "soc_network_operations_version",
            (REPO / "scripts/ci/promote-app-pins.py").read_text(),
        )
        self.assertIn("soc_changed and soc_ready", deployment)
        self.assertIn('add_once("firewall", "vault,log,loop,soc")', deployment)
        self.assertIn("agent_core_coordinator", deployment)

    def test_soc_destination_firewalls_and_scrape_target_are_declared(self):
        vault_vars = (REPO / "ansible/inventory/host_vars/vault.yml").read_text()
        log_vars = (REPO / "ansible/inventory/host_vars/log.yml").read_text()
        prometheus = (REPO / "configs/mon/prometheus.yml").read_text()
        generated_vault = (REPO / "ansible/generated/vault/nftables.conf").read_text()
        generated_log = (REPO / "ansible/generated/log/nftables.conf").read_text()

        self.assertIn('"{{ peers.soc.ipv6 }}"', vault_vars)
        self.assertIn('src: "{{ peers.soc.ipv6 }}"', log_vars)
        self.assertIn("[2a0c:b641:b50:2::100]:9100", prometheus)
        self.assertIn("2a0c:b641:b50:2::100 } tcp dport 8200", generated_vault)
        self.assertIn(
            '2a0c:b641:b50:2::100 tcp dport 6000 counter accept comment "Vector ingest from soc"',
            generated_log,
        )


if __name__ == "__main__":
    unittest.main()
