import contextlib
import io
import json
import re
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class AppPromotionDeployTest(unittest.TestCase):
    def test_retirement_selects_noc_firewall_prerequisite(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/app-promotion-deploy.yml").read_text())
        step = next(step for step in workflow["jobs"]["detect"]["steps"] if step.get("id") == "detect")
        code = re.search(r"<<'PY'[^\n]*\n(.*?)\nPY", step["run"], re.S).group(1)
        output = io.StringIO()
        changed = "ansible/inventory/host_vars/noc.yml\nansible/inventory/host_vars/loop.yml"
        with patch.object(sys, "argv", ["detect", changed]), contextlib.redirect_stdout(output):
            exec(compile(code, "workflow-detector", "exec"), {})
        values = dict(line.split("=", 1) for line in output.getvalue().splitlines())
        self.assertIn({"playbook": "firewall", "limit": "noc"}, json.loads(values["firewall_matrix"])["include"])
        consumers = json.loads(values["matrix"])["include"]
        self.assertIn({"playbook": "noc", "limit": "noc"}, consumers)
        self.assertIn({"playbook": "retire-loop", "limit": "loop"}, consumers)
        self.assertEqual(workflow["jobs"]["apply"]["needs"], ["detect", "firewall"])

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

    def test_retired_loop_cannot_be_automatically_redeployed(self):
        workflow_text = (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()
        self.assertNotIn('add_once("engineering-loop", "loop")', workflow_text)
        self.assertNotIn("ansible/roles/agentic_observatory/**", workflow_text)
        self.assertNotIn("ansible/roles/knowledge_loop/**", workflow_text)
        self.assertIn('add_once("retire-loop", "loop")', workflow_text)
        self.assertIn("ansible/playbooks/retire-loop.yml", workflow_text)

    def test_prometheus_config_and_rules_changes_trigger_mon_apply(self):
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

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
        workflow_text = (
            REPO / ".github/workflows/app-promotion-deploy.yml"
        ).read_text()

        self.assertIn("ansible/inventory/host_vars/mon.yml", workflow_text)
        self.assertIn(
            'mon_firewall_changed = "ansible/inventory/host_vars/mon.yml" in changed',
            workflow_text,
        )
        firewall = workflow_text.index('add_firewall_once("mon")')
        prometheus = workflow_text.index('add_once("prometheus", "mon")')
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
