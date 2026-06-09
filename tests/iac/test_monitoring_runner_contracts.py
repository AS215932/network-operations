"""Monitoring contracts for the two self-hosted GitHub Actions runners."""

import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _prometheus_targets():
    cfg = yaml.safe_load((REPO / "configs/mon/prometheus.yml").read_text())
    targets = set()
    labels_by_target = {}
    for job in cfg["scrape_configs"]:
        for static in job.get("static_configs", []):
            labels = static.get("labels", {})
            for target in static.get("targets", []):
                targets.add(target)
                labels_by_target[target] = labels
    return targets, labels_by_target


class MonitoringRunnerContractsTest(unittest.TestCase):
    def test_prometheus_scrapes_both_github_runner_hosts(self):
        targets, labels = _prometheus_targets()

        expected = {
            "[2a0c:b641:b50:2::d0]:9100": "infra",  # ci
            "[2a0c:b641:b51::c1]:9100": "ci-pr",   # unprivileged PR runner
        }
        for target, role in expected.items():
            with self.subTest(target=target):
                self.assertIn(target, targets)
                self.assertEqual(labels[target].get("role"), role)

    def test_icinga_runner_checks_are_host_var_driven(self):
        service_cfg = (REPO / "configs/mon/icinga2/services/github-runner.conf").read_text()
        self.assertIn(
            "assign where host.vars.github_runner && host.vars.prom_instance_node",
            service_cfg,
        )
        self.assertNotIn('assign where host.name == "ci"', service_cfg)

        for host in ("ci", "ci-pr"):
            with self.subTest(host=host):
                host_vars = yaml.safe_load(
                    (REPO / f"ansible/inventory/host_vars/{host}.yml").read_text()
                )
                self.assertTrue(host_vars["monitoring_check_vars"]["github_runner"])
                rendered = (REPO / f"ansible/generated/{host}/icinga_host.conf").read_text()
                self.assertIn("vars.github_runner = true", rendered)


if __name__ == "__main__":
    unittest.main()
