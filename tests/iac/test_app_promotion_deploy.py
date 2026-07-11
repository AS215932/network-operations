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

    def test_agentic_observatory_changes_trigger_loop_apply(self):
        workflow_text = (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()

        self.assertIn("ansible/roles/agentic_observatory/**", workflow_text)
        self.assertIn("ansible/roles/agentic_observatory \\", workflow_text)
        self.assertIn(
            "ansible/roles/vault_agent/templates/agentic-observatory.env.ctmpl.j2",
            workflow_text,
        )
        self.assertIn('"ansible/roles/agentic_observatory/"', workflow_text)

    def test_knowledge_loop_role_changes_trigger_loop_apply(self):
        workflow_text = (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()

        self.assertIn("ansible/roles/knowledge_loop/**", workflow_text)
        self.assertIn("ansible/roles/knowledge_loop \\", workflow_text)
        self.assertIn("ansible/roles/vault_agent/templates/knowledge-loop.env.ctmpl.j2", workflow_text)
        self.assertIn(
            "ansible/roles/vault_agent/templates/knowledge-loop-github-app-key.pem.ctmpl.j2",
            workflow_text,
        )
        self.assertIn('"ansible/roles/knowledge_loop/"', workflow_text)

    def test_prometheus_config_and_rules_changes_trigger_mon_apply(self):
        workflow_text = (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()

        # Trigger paths, git-diff scope, and detect logic must all cover the
        # mon Prometheus rules so a rule edit deploys via apply.yml → mon.
        self.assertIn("configs/mon/prometheus-rules/**", workflow_text)
        self.assertIn("configs/mon/prometheus-rules \\", workflow_text)
        self.assertIn("configs/mon/prometheus.yml", workflow_text)
        self.assertIn("configs/mon/blackbox.yml", workflow_text)
        self.assertIn("ansible/roles/prometheus/**", workflow_text)
        self.assertIn('path.startswith("configs/mon/prometheus-rules/")', workflow_text)
        self.assertIn('path == "configs/mon/prometheus.yml"', workflow_text)
        self.assertIn('path == "configs/mon/blackbox.yml"', workflow_text)
        self.assertIn('add_once("prometheus", "mon")', workflow_text)

    def test_mon_firewall_changes_apply_before_prometheus(self):
        workflow_text = (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()

        self.assertIn("ansible/inventory/host_vars/mon.yml", workflow_text)
        self.assertIn(
            'mon_firewall_changed = "ansible/inventory/host_vars/mon.yml" in changed',
            workflow_text,
        )
        firewall = workflow_text.index('add_once("firewall", "mon")')
        prometheus = workflow_text.index('add_once("prometheus", "mon")')
        self.assertLess(firewall, prometheus)

    def test_alertmanager_changes_trigger_mon_apply(self):
        workflow_text = (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()

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
