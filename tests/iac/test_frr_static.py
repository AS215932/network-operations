import json
import re
import sys
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "ansible/inventory"
HOST_VARS = INVENTORY / "host_vars"
ROUTERS_VARS = INVENTORY / "group_vars/routers.yml"
CONFIGS = REPO / "configs"
GENERATED = REPO / "ansible/generated"
POLICY_INTENT = REPO / "configs/frr-policy-intent.yml"

sys.path.insert(0, str(REPO / "scripts/netops"))
from frr_semantic import parse_frr_config, semantic_json  # noqa: E402
from render_frr_policy import effective_policy, load_intent, policy_json, render_policy_conf  # noqa: E402


def load_yaml(path):
    return yaml.safe_load(path.read_text()) or {}


def router_hosts():
    hosts = load_yaml(INVENTORY / "hosts.yml")
    return sorted(hosts["all"]["children"]["routers"]["hosts"])


ROUTERS = router_hosts()
FRR_FILES = {router: CONFIGS / router / "frr.conf" for router in ROUTERS}
ALL_VARS = load_yaml(INVENTORY / "group_vars/all.yml")
CORE_LOOPBACKS = {router: ALL_VARS["peers"][router]["loopback"] for router in ROUTERS}
POLICY_INTENT_DATA = load_intent(POLICY_INTENT)


def frr_text(node):
    return FRR_FILES[node].read_text()


def neighbors(text):
    result = {}
    for addr, asn in re.findall(r"^\s*neighbor\s+(\S+)\s+remote-as\s+(\d+)", text, re.M):
        result[addr] = int(asn)
    return result


class FrrStaticTest(unittest.TestCase):
    def test_every_router_has_committed_frr_config(self):
        self.assertEqual(set(ROUTERS), {"rtr", "cr1-nl1", "cr1-de1", "cr1-ch1"})
        for node, path in FRR_FILES.items():
            with self.subTest(node=node):
                self.assertTrue(path.exists(), f"{node} missing {path}")
                self.assertGreater(path.stat().st_size, 0, f"{node} config is empty")

    def test_semantic_artifacts_are_current(self):
        for node, path in FRR_FILES.items():
            artifact = GENERATED / node / "frr-semantic.json"
            with self.subTest(node=node):
                self.assertTrue(artifact.exists(), f"run scripts/netops/frr_semantic.py --all; missing {artifact}")
                expected = semantic_json(parse_frr_config(path, host=node))
                # Validate JSON first so failures clearly distinguish malformed
                # artifacts from stale-but-valid artifacts.
                json.loads(artifact.read_text())
                self.assertEqual(artifact.read_text(), expected)

    def test_policy_intent_artifacts_are_current(self):
        for node in FRR_FILES:
            policy = effective_policy(POLICY_INTENT_DATA, node)
            json_artifact = GENERATED / node / "frr-policy.json"
            conf_artifact = GENERATED / node / "frr-policy.conf"
            with self.subTest(node=node, artifact="json"):
                self.assertTrue(json_artifact.exists(), f"run scripts/netops/render_frr_policy.py; missing {json_artifact}")
                json.loads(json_artifact.read_text())
                self.assertEqual(json_artifact.read_text(), policy_json(policy))
            with self.subTest(node=node, artifact="conf"):
                self.assertTrue(conf_artifact.exists(), f"run scripts/netops/render_frr_policy.py; missing {conf_artifact}")
                self.assertEqual(conf_artifact.read_text(), render_policy_conf(policy))

    def test_policy_intent_matches_committed_frr_policy(self):
        for node, path in FRR_FILES.items():
            semantic = parse_frr_config(path, host=node)
            policy = effective_policy(POLICY_INTENT_DATA, node)
            with self.subTest(node=node, section="prefix-lists"):
                actual = {}
                for name in policy["ipv6_prefix_lists"]:
                    actual[name] = [
                        {"seq": item["seq"], "action": item["action"], "value": item["value"]}
                        for item in semantic["prefix_lists"]["ipv6"].get(name, [])
                    ]
                self.assertEqual(actual, policy["ipv6_prefix_lists"])
            with self.subTest(node=node, section="as-path"):
                actual = {}
                for name in policy["as_path_access_lists"]:
                    actual[name] = [
                        {k: item[k] for k in ("seq", "action", "pattern") if item.get(k) is not None}
                        for item in semantic["as_path_access_lists"].get(name, [])
                    ]
                self.assertEqual(actual, policy["as_path_access_lists"])
            with self.subTest(node=node, section="route-maps"):
                actual = {}
                for name in policy["route_maps"]:
                    actual[name] = []
                    sequences = semantic["route_maps"].get(name, {}).get("sequences", {})
                    for seq in sorted(sequences.values(), key=lambda item: item["seq"]):
                        actual[name].append(
                            {
                                "seq": seq["seq"],
                                "action": seq["action"],
                                "matches": seq["matches"],
                                "sets": seq["sets"],
                                "on_match": seq["on_match"],
                            }
                        )
                self.assertEqual(actual, policy["route_maps"])
            with self.subTest(node=node, section="neighbor-route-maps"):
                actual = {}
                for instance in semantic["bgp"]["instances"]:
                    af = instance["address_families"].get("ipv6 unicast", {})
                    for neighbor, data in af.get("neighbors", {}).items():
                        if data.get("route_maps"):
                            actual[neighbor] = data["route_maps"]
                self.assertEqual(actual, policy["neighbor_route_maps"])

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

    def test_route_maps_do_not_reference_undefined_as_path_lists(self):
        for node in FRR_FILES:
            text = frr_text(node)
            defined = set(re.findall(r"^bgp as-path access-list\s+(\S+)", text, re.M))
            referenced = set(re.findall(r"match as-path\s+(\S+)", text))
            self.assertTrue(referenced <= defined, f"{node}: undefined AS-path lists {referenced - defined}")

    def test_route_map_references_are_defined(self):
        for node in FRR_FILES:
            text = frr_text(node)
            defined = set(re.findall(r"^route-map\s+(\S+)\s+", text, re.M))
            referenced = set(re.findall(r"neighbor\s+\S+\s+route-map\s+(\S+)\s+(?:in|out)", text))
            self.assertTrue(referenced <= defined, f"{node}: undefined route-maps {referenced - defined}")

    def test_inventory_expected_frr_versions_match_config_headers(self):
        for node in FRR_FILES:
            host_vars = load_yaml(HOST_VARS / f"{node}.yml")
            expected = host_vars.get("frr_version_expected")
            match = re.search(r"^frr version\s+(\S+)", frr_text(node), re.M)
            with self.subTest(node=node):
                self.assertIsNotNone(expected, f"{node} has no frr_version_expected host var")
                self.assertIsNotNone(match, f"{node} config has no frr version line")
                self.assertEqual(expected, match.group(1))

    def test_production_netconf_endpoint_and_writes_are_disabled(self):
        router_defaults = load_yaml(ROUTERS_VARS)
        self.assertFalse(router_defaults.get("frr_netconf_endpoint_enabled", False))
        self.assertFalse(router_defaults.get("frr_netconf_write_enabled", False))
        for node in FRR_FILES:
            host_vars = load_yaml(HOST_VARS / f"{node}.yml")
            with self.subTest(node=node):
                self.assertFalse(host_vars.get("frr_netconf_endpoint_enabled", False))
                self.assertFalse(host_vars.get("frr_netconf_write_enabled", False))


if __name__ == "__main__":
    unittest.main()
