import json
import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class RtrNat64ContractsTest(unittest.TestCase):
    def test_pool_excludes_dnat_and_host_ephemeral_ports(self):
        config = json.loads((REPO / "configs/rtr/jool/jool.conf").read_text())
        nft = (REPO / "configs/rtr/nftables.conf").read_text()
        reserved = {"TCP": set(), "UDP": set()}
        for proto, expression in re.findall(
            r"\b(tcp|udp) dport (\{[^}]+\}|[0-9-]+) dnat", nft
        ):
            for part in expression.strip("{} ").split(","):
                bounds = [int(value) for value in part.strip().split("-")]
                reserved[proto.upper()].update(range(bounds[0], bounds[-1] + 1))
        self.assertTrue(reserved["TCP"])
        self.assertTrue(reserved["UDP"])
        sysctl = (REPO / "configs/rtr/sysctl.conf").read_text()
        low, high = map(int, re.search(
            r"net.ipv4.ip_local_port_range=(\d+) (\d+)", sysctl
        ).groups())
        for entry in config["pool4"]:
            proto = entry["protocol"]
            if proto == "ICMP":
                continue
            bounds = list(map(int, entry["port range"].split("-")))
            ports = set(range(bounds[0], bounds[-1] + 1))
            self.assertFalse(ports & reserved[proto], entry)
            self.assertFalse(ports & set(range(low, high + 1)), entry)

    def test_nat64_vrf_leak_routes_returns_to_overlay_clients(self):
        unit = (REPO / "configs/rtr/jool/nat64-vrf-leak.service").read_text()

        self.assertIn("RemainAfterExit=yes", unit)
        self.assertIn(
            "ExecStart=/usr/sbin/ip -6 rule add to 2a0c:b641:b51::/48 lookup 200 prio 900",
            unit,
        )
        self.assertIn(
            "ExecStart=/usr/sbin/ip -6 rule add to 2a0c:b641:b50:2::/64 lookup 200 prio 901",
            unit,
        )
        self.assertIn(
            "ExecStop=-/usr/sbin/ip -6 rule del to 2a0c:b641:b51::/48 lookup 200 prio 900",
            unit,
        )
        self.assertIn(
            "ExecStop=-/usr/sbin/ip -6 rule del to 2a0c:b641:b50:2::/64 lookup 200 prio 901",
            unit,
        )
        for line in unit.splitlines():
            if line.startswith("ExecStop="):
                self.assertTrue(line.startswith("ExecStop=-"), line)

    def test_firewall_handler_restores_nat64_leak_after_jool_restart(self):
        handlers = yaml.safe_load(
            (REPO / "ansible/roles/firewall/handlers/main.yml").read_text()
        )
        names = [handler.get("name") for handler in handlers]

        self.assertIn("restart jool", names)
        self.assertIn("restart nat64-vrf-leak after jool", names)
        self.assertLess(names.index("restart jool"), names.index("restart nat64-vrf-leak after jool"))

        jool_handler = next(handler for handler in handlers if handler.get("name") == "restart jool")
        nat64_handler = next(
            handler for handler in handlers if handler.get("name") == "restart nat64-vrf-leak after jool"
        )
        self.assertEqual(jool_handler["systemd"]["state"], "restarted")
        self.assertFalse(jool_handler["systemd"]["no_block"])
        self.assertEqual(nat64_handler["systemd"]["state"], "restarted")
        self.assertFalse(nat64_handler["systemd"]["no_block"])
        self.assertEqual(jool_handler["listen"], "reload nftables")
        self.assertEqual(nat64_handler["listen"], "reload nftables")

    def test_firewall_role_deploys_nat64_vrf_leak_unit_from_source(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/firewall/tasks/nftables.yml").read_text())
        task_by_name = {task.get("name"): task for task in tasks}

        review_task = task_by_name["Stage nat64-vrf-leak unit to controller (review artifact)"]
        self.assertEqual(
            review_task["copy"]["src"],
            "{{ playbook_dir }}/../../configs/rtr/jool/nat64-vrf-leak.service",
        )
        self.assertEqual(
            review_task["copy"]["dest"],
            "{{ firewall_generated_dir }}/{{ inventory_hostname }}/nat64-vrf-leak.service",
        )
        self.assertEqual(review_task["when"], 'inventory_hostname == "rtr"')

        install_task = task_by_name["Install nat64-vrf-leak unit on rtr"]
        self.assertEqual(
            install_task["copy"]["src"],
            "{{ playbook_dir }}/../../configs/rtr/jool/nat64-vrf-leak.service",
        )
        self.assertEqual(install_task["copy"]["dest"], "/etc/systemd/system/nat64-vrf-leak.service")
        self.assertEqual(install_task["notify"], "reload nftables")

        enable_task = task_by_name["Enable nat64-vrf-leak unit on rtr"]
        self.assertEqual(enable_task["systemd"]["name"], "nat64-vrf-leak")
        self.assertTrue(enable_task["systemd"]["enabled"])
        self.assertTrue(enable_task["systemd"]["daemon_reload"])
