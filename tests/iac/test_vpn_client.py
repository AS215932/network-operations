"""Static invariants for the IPv6-only AS215932 VPN client."""

import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "configs/vpn/client.conf"
README = REPO / "configs/vpn/README.md"
NETWORKMANAGER = REPO / "configs/vpn/90-as215932-client.conf"


class VPNClientTest(unittest.TestCase):
    def test_wireguard_carries_only_ipv6(self):
        text = CLIENT.read_text()
        allowed = re.search(r"^AllowedIPs\s*=\s*(.+)$", text, re.MULTILINE)

        self.assertIsNotNone(allowed)
        self.assertEqual(allowed.group(1).strip(), "::/0")
        self.assertNotIn("PostUp", text)
        self.assertNotIn("PostDown", text)

    def test_networkmanager_blackholes_ipv4_without_exceptions(self):
        client = CLIENT.read_text()
        readme = README.read_text()

        self.assertIn("0.0.0.0/0 type=blackhole", readme)
        self.assertNotIn("type=throw", readme)
        self.assertIn("priority 31021 from all table 333856", readme)
        self.assertIn("wireguard.fwmark 0x51820", readme)
        self.assertIn("allowed-ips=::/0", readme)
        self.assertIn(
            "Endpoint = [2a0c:b641:b50:2::60]:51820",
            client,
        )
        self.assertNotIn("Endpoint = 46.105.40.223:51820", client)
        self.assertNotIn("allowed-ips=::/0;0.0.0.0/0", readme)

    def test_networkmanager_does_not_penalize_the_underlay_route(self):
        config = NETWORKMANAGER.read_text()
        readme = README.read_text()

        self.assertRegex(config, r"(?m)^\[connectivity\]$")
        self.assertRegex(config, r"(?m)^enabled=false$")
        self.assertIn("ConnectivityCheckEnabled b false", readme)
        self.assertNotIn("ping.archlinux.org/32 type=throw", readme)


if __name__ == "__main__":
    unittest.main()
