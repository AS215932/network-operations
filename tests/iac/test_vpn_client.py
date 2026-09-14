"""Static invariants for the IPv6-only AS215932 VPN client."""

import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "configs/vpn/client.conf"
README = REPO / "configs/vpn/README.md"


class VPNClientTest(unittest.TestCase):
    def test_wireguard_carries_only_ipv6(self):
        text = CLIENT.read_text()
        allowed = re.search(r"^AllowedIPs\s*=\s*(.+)$", text, re.MULTILINE)

        self.assertIsNotNone(allowed)
        self.assertEqual(allowed.group(1).strip(), "::/0")
        self.assertNotIn("PostUp", text)
        self.assertNotIn("PostDown", text)

    def test_networkmanager_uses_a_real_ipv4_blackhole(self):
        text = README.read_text()

        self.assertIn('ipv4.routes "0.0.0.0/0 type=blackhole"', text)
        self.assertIn("not fwmark 0x51820 table 333856", text)
        self.assertIn("wireguard.fwmark 0x51820", text)
        self.assertIn("allowed-ips=::/0", text)
        self.assertNotIn("allowed-ips=::/0;0.0.0.0/0", text)


if __name__ == "__main__":
    unittest.main()
