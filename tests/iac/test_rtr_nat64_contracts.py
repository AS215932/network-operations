import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class RtrNat64ContractsTest(unittest.TestCase):
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
            "ExecStop=/usr/sbin/ip -6 rule del to 2a0c:b641:b51::/48 lookup 200 prio 900",
            unit,
        )
        self.assertIn(
            "ExecStop=/usr/sbin/ip -6 rule del to 2a0c:b641:b50:2::/64 lookup 200 prio 901",
            unit,
        )

    def test_firewall_handler_restores_nat64_leak_after_jool_restart(self):
        handlers = yaml.safe_load(
            (REPO / "ansible/roles/firewall/handlers/main.yml").read_text()
        )
        names = [handler.get("name") for handler in handlers]

        self.assertIn("restart jool", names)
        self.assertIn("restart nat64-vrf-leak", names)
        self.assertLess(names.index("restart jool"), names.index("restart nat64-vrf-leak"))

        nat64_handler = next(
            handler for handler in handlers if handler.get("name") == "restart nat64-vrf-leak"
        )
        self.assertEqual(nat64_handler["systemd"]["state"], "restarted")
        self.assertEqual(nat64_handler["listen"], "reload nftables")
