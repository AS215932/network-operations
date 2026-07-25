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


class ReturnPathSteeringTest(unittest.TestCase):
    """cr1-ch1 is the preferred path in both directions (#351).

    Egress is pinned by local-pref on rtr's iBGP sessions; ingress is pulled in by
    /48 more-specifics exported from cr1-ch1 alone, backed by 3x prepends on the
    nl1/de1 upstreams. See docs/bgp-policy.md.
    """

    MORE_SPECIFICS = ["2a0c:b641:b50::/48", "2a0c:b641:b51::/48"]

    def test_only_cr1_ch1_defines_the_more_specific_export_list(self):
        for node in FRR_FILES:
            defines = "ipv6 prefix-list AS215932v6-out-ch1" in frr_text(node)
            with self.subTest(node=node):
                self.assertEqual(defines, node == "cr1-ch1")

    def test_cr1_ch1_exports_aggregate_plus_both_more_specifics(self):
        permits = re.findall(
            r"^ipv6 prefix-list AS215932v6-out-ch1 seq \d+ permit (\S+)",
            frr_text("cr1-ch1"),
            re.M,
        )
        self.assertEqual(permits, ["2a0c:b641:b50::/44"] + self.MORE_SPECIFICS)

    def test_more_specifics_are_never_longer_than_the_roa_max_length(self):
        # The ROA for 2a0c:b641:b50::/44 (origin 215932) has maxLength 48.
        # Anything longer would be RPKI Invalid and dropped by validating peers.
        for prefix in re.findall(
            r"^ipv6 prefix-list AS215932v6-out-ch1 seq \d+ permit \S+/(\d+)",
            frr_text("cr1-ch1"),
            re.M,
        ):
            self.assertLessEqual(int(prefix), 48)

    def test_more_specifics_are_originated_by_rtr(self):
        text = frr_text("rtr")
        for prefix in self.MORE_SPECIFICS:
            with self.subTest(prefix=prefix):
                self.assertRegex(text, rf"(?m)^\s*network {re.escape(prefix)}\s*$")

    def test_cr1_ch1_export_maps_use_the_more_specific_list(self):
        text = frr_text("cr1-ch1")
        for name in ("TRANSIT-OUT", "IXP-OUT"):
            with self.subTest(route_map=name):
                block = re.search(rf"^route-map {name} permit \d+\n(.*?)^exit", text, re.M | re.S)
                self.assertIsNotNone(block, f"cr1-ch1 has no {name}")
                self.assertIn("prefix-list AS215932v6-out-ch1", block.group(1))

    def test_degraded_upstreams_are_prepended_and_ch1_is_not(self):
        expect_prepend = {"cr1-nl1": True, "cr1-de1": True, "cr1-ch1": False}
        for node, should_prepend in expect_prepend.items():
            text = frr_text(node)
            outbound = set(re.findall(r"neighbor\s+\S+\s+route-map\s+(\S+)\s+out", text))
            transit_maps = {m for m in outbound if "IXP" not in m}
            with self.subTest(node=node):
                self.assertTrue(transit_maps, f"{node} has no transit export map")
                prepending = {m for m in transit_maps if m.endswith("-PREPEND-3X")}
                if should_prepend:
                    self.assertEqual(prepending, transit_maps, f"{node}: unprepended transit export")
                else:
                    self.assertEqual(prepending, set(), f"{node}: must stay unprepended")

    def test_rtr_ibgp_local_pref_prefers_ch1_then_nl1_then_de1(self):
        text = frr_text("rtr")
        expected = {"2a0c:b641:b50::c": 200, "2a0c:b641:b50::a": 90, "2a0c:b641:b50::b": 80}
        seen = {}
        for loopback, want in expected.items():
            match = re.search(
                rf"neighbor\s+{re.escape(loopback)}\s+route-map\s+(\S+)\s+in", text
            )
            self.assertIsNotNone(match, f"rtr has no inbound route-map for {loopback}")
            block = re.search(
                rf"^route-map {match.group(1)} permit \d+\n(.*?)^exit", text, re.M | re.S
            )
            self.assertIsNotNone(block, f"rtr route-map {match.group(1)} is undefined")
            lp = re.search(r"set local-preference (\d+)", block.group(1))
            self.assertIsNotNone(lp, f"rtr route-map {match.group(1)} sets no local-preference")
            seen[loopback] = int(lp.group(1))
        self.assertEqual(seen, expected)
        ch1, nl1, de1 = (seen[k] for k in ("2a0c:b641:b50::c", "2a0c:b641:b50::a", "2a0c:b641:b50::b"))
        self.assertGreater(ch1, nl1)
        self.assertGreater(nl1, de1)

    def test_non_terminal_local_pref_clauses_fall_through_to_the_hygiene_filter(self):
        """A `set local-preference` clause that skips `on-match next` bypasses the
        shared as-path filter for every route it matches — the exact defect the
        live cr1-ch1 FASTLY clause carried before it was codified.

        Comment lines are stripped first: the surrounding config explains this rule
        in prose, and a substring match would be satisfied by the explanation.
        """
        for node in FRR_FILES:
            text = frr_text(node)
            blocks = re.findall(r"^route-map (\S+) permit (\d+)\n(.*?)^exit", text, re.M | re.S)
            highest = {}
            for name, seq, _ in blocks:
                highest[name] = max(highest.get(name, 0), int(seq))
            for name, seq, body in blocks:
                directives = [
                    line.strip()
                    for line in body.splitlines()
                    if line.strip() and not line.lstrip().startswith("!")
                ]
                sets_lp = any(d.startswith("set local-preference") for d in directives)
                if not sets_lp or int(seq) == highest[name]:
                    continue
                with self.subTest(node=node, route_map=name, seq=seq):
                    self.assertIn("on-match next", directives)


if __name__ == "__main__":
    unittest.main()
