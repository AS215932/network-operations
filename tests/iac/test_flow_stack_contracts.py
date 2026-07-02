import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "ansible/inventory"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text())


class FlowStackContractsTest(unittest.TestCase):
    def test_flow_host_is_inventory_backed_and_monitored(self):
        hosts = _yaml(INVENTORY / "hosts.yml")["all"]["children"]
        peers = _yaml(INVENTORY / "group_vars/all.yml")["peers"]
        flow_vars = _yaml(INVENTORY / "host_vars/flow.yml")
        prometheus = (REPO / "configs/mon/prometheus.yml").read_text()

        self.assertEqual(
            hosts["linux"]["hosts"]["flow"]["ansible_host"],
            "2a0c:b641:b50:2::110",
        )
        self.assertIn("flow", hosts["infra_vms"]["hosts"])
        self.assertEqual(peers["flow"]["ipv6"], "2a0c:b641:b50:2::110")
        self.assertTrue(flow_vars["monitoring_register"])
        self.assertTrue(flow_vars["logs_register"])
        self.assertTrue(flow_vars["flow_collector_enabled"])
        self.assertIn("[2a0c:b641:b50:2::110]:9100", prometheus)

    def test_flow_firewall_contract_has_only_internal_ingress(self):
        flow_vars = _yaml(INVENTORY / "host_vars/flow.yml")
        rules = flow_vars["firewall_extra_rules"]

        flow_export = next(
            rule for rule in rules if rule["comment"] == "flow export from routers"
        )
        self.assertEqual(flow_export["proto"], "udp")
        self.assertEqual(flow_export["dport"], [2055, 4739, 6343])
        self.assertIn("{{ router_loopback_subnet }}", flow_export["src"])
        self.assertIn("{{ wg_link_prefix }}", flow_export["src"])

        ui = next(
            rule for rule in rules if rule["comment"] == "nfsen-ng internal HTTP UI"
        )
        self.assertEqual(ui["proto"], "tcp")
        self.assertEqual(ui["dport"], 80)
        self.assertNotEqual(ui["src"], "any")
        self.assertIn("{{ vpn_clients_subnet }}", ui["src"])
        self.assertIn("{{ peers.proxy.ipv6 }}/128", ui["src"])

    def test_all_core_routers_export_sampled_flows_to_collector(self):
        expected_interfaces = {
            "rtr": {"wan", "infra", "customer"},
            "cr1-nl1": {"transit", "ixp-nl"},
            "cr1-de1": {"transit1", "transit2", "ixp-dus", "ixp-fra"},
            "cr1-ch1": {"transit", "ixp-4ixp", "ixp-sbix"},
        }

        for host, names in expected_interfaces.items():
            with self.subTest(host=host):
                host_vars = _yaml(INVENTORY / f"host_vars/{host}.yml")
                self.assertTrue(host_vars["flow_exporter_enabled"])
                self.assertEqual(
                    {item["name"] for item in host_vars["flow_exporter_interfaces"]},
                    names,
                )

    def test_flow_roles_pin_lightweight_components(self):
        collector_defaults = (
            REPO / "ansible/roles/flow_collector/defaults/main.yml"
        ).read_text()
        exporter_defaults = (
            REPO / "ansible/roles/flow_exporter/defaults/main.yml"
        ).read_text()

        self.assertIn("nfdump", collector_defaults)
        self.assertIn("php-rrd", collector_defaults)
        self.assertIn("v1.0-RC.1", collector_defaults)
        self.assertIn("flow_exporter_sampling_rate: 1000", exporter_defaults)
        self.assertIn("flow_exporter_version: 9", exporter_defaults)

    def test_flow_generated_monitoring_checks_collector_process_not_udp_reply(self):
        rendered = (REPO / "ansible/generated/flow/icinga_host.conf").read_text()

        self.assertIn('object Service "netflow-collector"', rendered)
        self.assertIn('check_command = "prom_systemd_unit"', rendered)
        self.assertIn(
            'vars.systemd_unit = "flow-nfcapd-netflow.service"',
            rendered,
        )
        self.assertNotIn('check_command = "udp"', rendered)


if __name__ == "__main__":
    unittest.main()
