import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class AppPromotionDeployTest(unittest.TestCase):
    def test_apply_matrix_is_serialized(self):
        workflow = yaml.safe_load(
            (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()
        )

        strategy = workflow["jobs"]["apply"]["strategy"]
        self.assertEqual(strategy["fail-fast"], False)
        self.assertEqual(strategy["max-parallel"], 1)

    def test_firewalls_gate_dependent_consumer_applies(self):
        workflow = yaml.safe_load(
            (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()
        )

        firewall = workflow["jobs"]["firewall"]
        apply = workflow["jobs"]["apply"]
        self.assertEqual(firewall["needs"], "detect")
        self.assertEqual(firewall["strategy"]["fail-fast"], True)
        self.assertEqual(firewall["strategy"]["max-parallel"], 1)
        self.assertNotIn("extmon", workflow["jobs"])
        self.assertEqual(apply["needs"], ["detect", "firewall"])
        self.assertIn("needs.firewall.result == 'success'", apply["if"])
        self.assertIn("needs.firewall.result == 'skipped'", apply["if"])

    def test_agentic_observatory_changes_trigger_loop_apply(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        self.assertIn("ansible/roles/agentic_observatory/**", workflow_text)
        self.assertIn("ansible/roles/agentic_observatory \\", workflow_text)
        self.assertIn(
            "ansible/roles/vault_agent/templates/agentic-observatory.env.ctmpl.j2",
            workflow_text,
        )
        self.assertIn('"ansible/roles/agentic_observatory/"', workflow_text)

    def test_knowledge_loop_role_changes_trigger_loop_apply(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        self.assertIn("ansible/roles/knowledge_loop/**", workflow_text)
        self.assertIn("ansible/roles/knowledge_loop \\", workflow_text)
        self.assertIn(
            "ansible/roles/vault_agent/templates/knowledge-loop.env.ctmpl.j2",
            workflow_text,
        )
        self.assertIn(
            "ansible/roles/vault_agent/templates/knowledge-loop-github-app-key.pem.ctmpl.j2",
            workflow_text,
        )
        self.assertIn('"ansible/roles/knowledge_loop/"', workflow_text)

    def test_prometheus_config_and_rules_changes_trigger_mon_apply(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        # Trigger paths, git-diff scope, and detect logic must cover the main
        # scrape/blackbox configs and rules so none remains repository-only.
        self.assertIn("configs/mon/prometheus.yml \\", workflow_text)
        self.assertIn("configs/mon/prometheus-rules/**", workflow_text)
        self.assertIn("configs/mon/prometheus-rules \\", workflow_text)
        self.assertIn("configs/mon/blackbox.yml", workflow_text)
        self.assertIn("ansible/roles/prometheus/**", workflow_text)
        self.assertIn('path == "configs/mon/prometheus.yml"', workflow_text)
        self.assertIn('path.startswith("configs/mon/prometheus-rules/")', workflow_text)
        self.assertIn('path == "configs/mon/blackbox.yml"', workflow_text)
        self.assertIn('add_once("prometheus", "mon")', workflow_text)

        install = (REPO / "ansible/roles/prometheus/tasks/install.yml").read_text()
        defaults = (REPO / "ansible/roles/prometheus/defaults/main.yml").read_text()
        self.assertIn("Validate staged Prometheus config", install)
        self.assertIn("Publish validated Prometheus core config", install)
        self.assertIn("prometheus_config_repo", defaults)

    def test_zone_changes_trigger_serialized_knot_apply(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        self.assertIn("configs/*.zone", workflow_text)
        self.assertIn("'configs/*.zone' \\", workflow_text)
        self.assertIn("ansible/roles/knot/**", workflow_text)
        self.assertIn("ansible/roles/knot \\", workflow_text)
        self.assertIn('path.endswith(".zone")', workflow_text)
        self.assertIn('add_once("knot", "nameservers")', workflow_text)

    def test_soc_firewall_changes_gate_soc_apply(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        firewall = workflow_text.index(
            'add_firewall_once("vault,log,loop,soc")'
        )
        soc = workflow_text.index('add_once("soc", "soc")')
        self.assertLess(firewall, soc)
        self.assertNotIn('add_once("firewall", "vault,log,loop,soc")', workflow_text)

    def test_mon_firewall_changes_apply_before_prometheus(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        self.assertIn("ansible/inventory/host_vars/mon.yml", workflow_text)
        self.assertIn(
            'mon_firewall_changed = "ansible/inventory/host_vars/mon.yml" in changed',
            workflow_text,
        )
        firewall = workflow_text.index('add_firewall_once("mon")')
        engineering_loop = workflow_text.index('add_once("engineering-loop", "loop")')
        prometheus = workflow_text.index('add_once("prometheus", "mon")')
        self.assertLess(firewall, engineering_loop)
        self.assertLess(firewall, prometheus)

    def test_extmon_firewall_changes_apply_before_prometheus(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        self.assertIn("ansible/inventory/host_vars/extmon.yml", workflow_text)
        self.assertIn(
            'extmon_firewall_changed = "ansible/inventory/host_vars/extmon.yml" in changed',
            workflow_text,
        )
        firewall = workflow_text.index('add_firewall_once("extmon")')
        prometheus = workflow_text.index('add_once("prometheus", "mon")')
        self.assertLess(firewall, prometheus)

    def test_extmon_module_is_rendered_but_not_auto_applied_without_secrets(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        self.assertNotIn("ansible/roles/extmon/**", workflow_text)
        self.assertNotIn("needs: [detect, firewall, extmon]", workflow_text)

        render_script = (REPO / "scripts/ci/render-all.sh").read_text()
        self.assertIn("prometheus alertmanager ci extmon", render_script)

        install = (
            REPO / "ansible" / "roles" / "prometheus" / "tasks" / "install.yml"
        ).read_text()
        self.assertIn("Verify required off-net blackbox module is deployed", install)
        self.assertIn("prometheus_extmon_strict_probe_check_url", install)

    def test_alertmanager_changes_trigger_mon_apply(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        # Trigger paths, git-diff scope, and detect logic must all cover the mon
        # Alertmanager role/template so a delivery-config edit deploys via
        # apply.yml → mon (the endpoint Prometheus routes alerts to).
        self.assertIn("configs/mon/alertmanager.yml.j2", workflow_text)
        self.assertIn("ansible/roles/alertmanager/**", workflow_text)
        self.assertIn("ansible/roles/alertmanager \\", workflow_text)
        self.assertIn("ansible/playbooks/alertmanager.yml", workflow_text)
        self.assertIn('path == "configs/mon/alertmanager.yml.j2"', workflow_text)
        self.assertIn('add_once("alertmanager", "mon")', workflow_text)


if __name__ == "__main__":
    unittest.main()
