import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
FRR_FILES = {
    "rtr": REPO / "configs/rtr/frr.conf",
    "cr1-nl1": REPO / "configs/cr1-nl1/frr.conf",
    "cr1-de1": REPO / "configs/cr1-de1/frr.conf",
    "cr1-ch1": REPO / "configs/cr1-ch1/frr.conf",
}
CORE_LOOPBACKS = {
    "rtr": "2a0c:b641:b50::d",
    "cr1-nl1": "2a0c:b641:b50::a",
    "cr1-de1": "2a0c:b641:b50::b",
    "cr1-ch1": "2a0c:b641:b50::c",
}


def frr_text(node):
    return FRR_FILES[node].read_text()


def neighbors(text):
    result = {}
    for addr, asn in re.findall(r"^\s*neighbor\s+(\S+)\s+remote-as\s+(\d+)", text, re.M):
        result[addr] = int(asn)
    return result


class FrrStaticTest(unittest.TestCase):
    def test_bgp_router_ids_are_unique(self):
        ids = {}
        for node in FRR_FILES:
            match = re.search(r"^\s*bgp router-id\s+(\S+)", frr_text(node), re.M)
            self.assertIsNotNone(match, f"{node} has no BGP router-id")
            ids[node] = match.group(1)
        self.assertEqual(len(ids), len(set(ids.values())), ids)

    def test_core_ibgp_full_mesh_is_configured(self):
        for src in FRR_FILES:
            configured = neighbors(frr_text(src))
            for dst, dst_loopback in CORE_LOOPBACKS.items():
                if src == dst:
                    continue
                with self.subTest(src=src, dst=dst):
                    self.assertEqual(configured.get(dst_loopback), 215932)

    def test_external_bgp_neighbors_have_inbound_and_outbound_route_maps(self):
        for node in FRR_FILES:
            text = frr_text(node)
            configured = neighbors(text)
            for addr, asn in configured.items():
                if asn == 215932:
                    continue
                with self.subTest(node=node, neighbor=addr):
                    self.assertRegex(text, rf"neighbor\s+{re.escape(addr)}\s+route-map\s+\S+\s+in")
                    self.assertRegex(text, rf"neighbor\s+{re.escape(addr)}\s+route-map\s+\S+\s+out")

    def test_transit_export_prefix_list_only_permits_canonical_aggregate(self):
        for node in FRR_FILES:
            permits = re.findall(
                r"^ipv6 prefix-list AS215932v6-out seq \d+ permit (\S+)",
                frr_text(node),
                re.M,
            )
            self.assertEqual(permits, ["2a0c:b641:b50::/44"], node)

    def test_route_maps_do_not_reference_undefined_prefix_lists(self):
        for node in FRR_FILES:
            text = frr_text(node)
            defined = set(re.findall(r"^ipv6 prefix-list\s+(\S+)", text, re.M))
            referenced = set(re.findall(r"match ipv6 address prefix-list\s+(\S+)", text))
            self.assertTrue(referenced <= defined, f"{node}: undefined prefix-lists {referenced - defined}")

    def test_route_map_references_are_defined(self):
        for node in FRR_FILES:
            text = frr_text(node)
            defined = set(re.findall(r"^route-map\s+(\S+)\s+", text, re.M))
            referenced = set(re.findall(r"neighbor\s+\S+\s+route-map\s+(\S+)\s+(?:in|out)", text))
            self.assertTrue(referenced <= defined, f"{node}: undefined route-maps {referenced - defined}")

    def test_route_maps_do_not_reference_undefined_as_path_lists(self):
        for node in FRR_FILES:
            text = frr_text(node)
            defined = set(re.findall(r"^bgp as-path access-list\s+(\S+)", text, re.M))
            referenced = set(re.findall(r"match as-path\s+(\S+)", text))
            self.assertTrue(referenced <= defined, f"{node}: undefined as-path lists {referenced - defined}")


if __name__ == "__main__":
    unittest.main()
