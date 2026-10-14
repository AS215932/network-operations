"""rtr-bsd runs OpenBSD base bgpd/ospf6d (issue #14, decided 2026-10-14).

These pin the policy translated from the former configs/rtr-bsd/frr.conf.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BGPD = (REPO / "configs/rtr-bsd/bgpd.conf").read_text()
OSPF6D = (REPO / "configs/rtr-bsd/ospf6d.conf").read_text()
RTR = (REPO / "configs/rtr/frr.conf").read_text()
MESH = {"2a0c:b641:b50::a", "2a0c:b641:b50::b", "2a0c:b641:b50::c", "2a0c:b641:b50::d"}


def code(text):
    return "\n".join(l.split("#", 1)[0] for l in text.splitlines())


class RtrBsdBgpdTest(unittest.TestCase):
    def test_identity(self):
        self.assertRegex(code(BGPD), r"(?m)^AS 215932$")
        self.assertRegex(code(BGPD), r"(?m)^router-id 0\.0\.0\.14$")
        self.assertRegex(code(OSPF6D), r"(?m)^router-id 0\.0\.0\.14$")

    def test_full_ibgp_mesh_from_loopback_with_nexthop_self(self):
        c = code(BGPD)
        self.assertEqual(set(re.findall(r"neighbor (\S+) \{", c)), MESH)
        self.assertIn("local-address 2a0c:b641:b50::e", c)
        self.assertIn("set nexthop self", c)
        self.assertIn("remote-as 215932", c)
        self.assertNotRegex(c, r"remote-as (?!215932)\d+")  # transit-free

    def test_burn_in_neighbors_start_down(self):
        self.assertRegex(code(BGPD), r"(?m)^\s+down$")

    def test_burn_in_announcements_lose_to_rtr(self):
        m = re.search(r"allow to group \"ibgp\" prefix-set AS215932v6-out set \{ localpref (\d+)", code(BGPD))
        self.assertIsNotNone(m)
        self.assertLess(int(m.group(1)), 100)
        self.assertRegex(code(BGPD), r"(?m)^deny\s+to group \"ibgp\"$")

    def test_egress_ladder_mirrors_rtr(self):
        c = code(BGPD)
        seen = {a: int(lp) for a, lp in re.findall(r"match from (\S+) set localpref (\d+)", c)}
        self.assertEqual(seen, {"2a0c:b641:b50::c": 200, "2a0c:b641:b50::a": 90, "2a0c:b641:b50::b": 80})

    def test_same_originated_set_as_rtr(self):
        rtr = set(re.findall(r"(?m)^\s*network (\S+)\s*$", RTR))
        bsd = set(re.findall(r"(?m)^network (\S+)$", code(BGPD)))
        self.assertTrue({"2a0c:b641:b50::/48", "2a0c:b641:b51::/48", "2a0c:b641:b50::/44"} <= bsd)
        self.assertEqual(bsd, rtr)

    def test_ospf6_loopback_passive_only(self):
        c = code(OSPF6D)
        self.assertRegex(c, r"interface lo0 \{ passive \}")
        for wg in ("wg0", "wg1", "wg2"):
            self.assertRegex(c, rf"interface {wg} \{{ type p2p \}}")
        self.assertNotIn("redistribute", c)


if __name__ == "__main__":
    unittest.main()
