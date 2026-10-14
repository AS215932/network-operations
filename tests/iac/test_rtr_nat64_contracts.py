import base64
import json
import re
import unittest
from pathlib import Path

import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment


REPO = Path(__file__).resolve().parents[2]


class RtrNat64ContractsTest(unittest.TestCase):
    def test_pool_excludes_dnat_and_host_ephemeral_ports(self):
        config = json.loads((REPO / "configs/rtr/jool/jool.conf").read_text())
        nft = (REPO / "ansible/roles/firewall/templates/nftables-rtr.conf.j2").read_text()
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

    def test_jool_config_is_rendered_and_installed_with_existing_handler_chain(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/firewall/tasks/nftables.yml").read_text())
        stages = {task["name"]: task for task in tasks if "name" in task}
        stage = stages["Stage Jool configuration to controller (review artifact)"]
        install = stages["Install Jool configuration on rtr"]
        self.assertEqual(stage["copy"]["src"], install["copy"]["src"])
        self.assertEqual(stage["delegate_to"], "localhost")
        self.assertFalse(stage["become"])
        self.assertIn("validate", stage["tags"])
        self.assertEqual(install["copy"]["dest"], "/etc/jool/jool.conf")
        self.assertTrue(install["copy"]["backup"])
        self.assertIn("firewall_apply | default(false) | bool", install["when"])
        self.assertIn('inventory_hostname == "rtr"', install["when"])
        self.assertEqual(install["tags"], ["apply"])
        self.assertEqual(install["notify"], "reload nftables")

    def test_retry_reconciles_unchanged_file_until_success_stamp_advances(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/firewall/tasks/nftables.yml").read_text())
        retry = next(t for t in tasks if t.get("name") == "Reconcile Jool configuration after interrupted applies")
        self.assertEqual(retry["notify"], "reload nftables")
        self.assertEqual(retry["tags"], ["apply"])
        env = NativeEnvironment(undefined=StrictUndefined)
        env.filters["b64decode"] = lambda value: base64.b64decode(value).decode()
        condition = env.from_string("{{ " + retry["changed_when"] + " }}")
        for stored, expected in [(None, True), ("old-checksum", True), ("desired-checksum\n", False)]:
            with self.subTest(stored=stored):
                stamp = {} if stored is None else {"content": base64.b64encode(stored.encode()).decode()}
                self.assertEqual(condition.render(
                    _jool_applied_config=stamp, _jool_desired_checksum="desired-checksum"
                ), expected)
        names = [t["name"] for t in tasks]
        record = next(t for t in tasks if t["name"] == "Record successfully applied Jool configuration")
        self.assertGreater(names.index(record["name"]), names.index("Cancel rollback watchdog"))
        self.assertGreater(names.index("Cancel rollback watchdog"), names.index("Flush handlers now (so watchdog cancellation reflects current state)"))
        self.assertEqual(record["copy"]["dest"], "/etc/jool/managed-config.sha256")
        self.assertIn("not ansible_check_mode", record["when"])
        self.assertIn("firewall_apply | default(false) | bool", record["when"])
        for task in tasks[:names.index(record["name"]) + 1]:
            self.assertFalse(task.get("ignore_errors", False))


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
