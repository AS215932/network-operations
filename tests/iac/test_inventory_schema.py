"""Tier-0 source-of-truth schema validation for the AS215932 inventory.

This is the schema gate the Wave 5 plan calls for, implemented stdlib-only
(``unittest`` + PyYAML) to match the rest of ``tests/iac`` and keep
``iac-static.sh`` dependency-light — no pydantic/jsonschema runtime dep.

It validates the *structure and internal consistency* of the inventory
source-of-truth (``hosts.yml`` + ``group_vars/all.yml``) before anything is
rendered, so addressing drift, a mis-segmented host, or an inconsistency
between the ``peers`` map and ``hosts.yml`` fails fast instead of producing a
silently-wrong rendered config. The customer/infra segmentation checks also
pin the two-runner isolation invariant (ci-pr must live on the customer
segment, never the infra segment) at the data layer.
"""

import ipaddress
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "ansible/inventory"
HOSTS = INVENTORY / "hosts.yml"
GROUP_VARS = INVENTORY / "group_vars/all.yml"

# Groups whose host entries are *references* to a canonical definition that
# lives in an OS/role group (linux/openbsd/freebsd/external/xcpng). They carry
# no ``ansible_host`` of their own.
REFERENCE_GROUPS = {"routers", "infra_vms", "public_facing", "nameservers"}

PLACEHOLDER_ADDRS = {"0.0.0.0", "::"}


def _ip(value):
    return ipaddress.ip_address(value)


def _net(value):
    return ipaddress.ip_network(value)


class InventorySchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = yaml.safe_load(HOSTS.read_text())
        cls.vars = yaml.safe_load(GROUP_VARS.read_text())

        children = cls.inv["all"]["children"]
        cls.groups = {
            name: dict((g or {}).get("hosts") or {}) for name, g in children.items()
        }

        # Canonical ansible_host per host, gathered from every group that
        # actually defines one. A host may appear in several groups; only the
        # definition(s) carrying ansible_host count.
        cls.host_addr = {}
        for hosts in cls.groups.values():
            for host, data in hosts.items():
                if isinstance(data, dict) and data.get("ansible_host"):
                    cls.host_addr.setdefault(host, data["ansible_host"])

        cls.infra = _net(cls.vars["infra_subnet"])
        cls.customer = _net(cls.vars["customer_subnet"])
        cls.prefix = _net(cls.vars["as215932_prefix"])
        cls.loopbacks = _net(cls.vars["router_loopback_subnet"])

    # ---- structure -------------------------------------------------------

    def test_inventory_parses_with_expected_top_level_groups(self):
        expected = {
            "linux",
            "external",
            "openbsd",
            "freebsd",
            "routers",
            "xcpng",
            "infra_vms",
            "public_facing",
            "nameservers",
        }
        self.assertTrue(expected.issubset(set(self.groups)))

    def test_every_referenced_host_has_a_canonical_address(self):
        for group in REFERENCE_GROUPS:
            for host in self.groups[group]:
                with self.subTest(group=group, host=host):
                    self.assertIn(
                        host,
                        self.host_addr,
                        f"{host} is referenced by group '{group}' but has no "
                        f"ansible_host definition in any OS/role group",
                    )

    def test_ansible_hosts_are_valid_and_unique(self):
        seen = {}
        for host, addr in self.host_addr.items():
            with self.subTest(host=host):
                ip = _ip(addr)  # raises if malformed
                if addr in PLACEHOLDER_ADDRS:
                    continue
                self.assertNotIn(
                    addr,
                    seen,
                    f"{host} reuses ansible_host {addr} already used by {seen.get(addr)}",
                )
                seen[addr] = host
                self.assertFalse(ip.is_loopback, f"{host} ansible_host is loopback")

    # ---- segmentation / isolation invariants ----------------------------

    def test_infra_vms_live_on_the_infra_segment(self):
        for host in self.groups["infra_vms"]:
            addr = self.host_addr[host]
            with self.subTest(host=host):
                self.assertIn(
                    _ip(addr),
                    self.infra,
                    f"infra VM {host} ({addr}) is outside infra_subnet {self.infra}",
                )

    def test_ci_pr_runner_is_isolated_on_the_customer_segment(self):
        # The two-runner security model requires the unprivileged PR runner to
        # sit on the customer-isolated segment, never the infra segment.
        addr = self.host_addr["ci-pr"]
        ip = _ip(addr)
        self.assertIn(ip, self.customer, f"ci-pr ({addr}) must be in customer_subnet")
        self.assertNotIn(ip, self.infra, f"ci-pr ({addr}) must NOT be in infra_subnet")
        self.assertNotIn(
            "ci-pr",
            self.groups["infra_vms"],
            "ci-pr must not be a member of the infra_vms group",
        )

    def test_routers_group_is_exactly_the_core_routers(self):
        self.assertEqual(
            set(self.groups["routers"]),
            {"rtr", "cr1-nl1", "cr1-de1", "cr1-ch1"},
        )

    def test_nameservers_are_a_subset_of_public_facing(self):
        ns = set(self.groups["nameservers"])
        self.assertEqual(ns, {"dns", "ns2"})
        self.assertTrue(ns.issubset(set(self.groups["public_facing"])))

    # ---- subnet plan -----------------------------------------------------

    def test_subnets_nest_under_the_allocation_and_are_disjoint(self):
        named = {
            "infra_subnet": self.infra,
            "customer_subnet": self.customer,
            "router_loopback_subnet": self.loopbacks,
            "vpn_clients_subnet": _net(self.vars["vpn_clients_subnet"]),
            "wg_link_prefix": _net(self.vars["wg_link_prefix"]),
        }
        for name, net in named.items():
            with self.subTest(subnet=name):
                self.assertTrue(
                    net.subnet_of(self.prefix),
                    f"{name} {net} is not within as215932_prefix {self.prefix}",
                )
        items = list(named.items())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (na, a), (nb, b) = items[i], items[j]
                with self.subTest(pair=f"{na}|{nb}"):
                    self.assertFalse(
                        a.overlaps(b), f"{na} {a} overlaps {nb} {b}"
                    )

    # ---- peers <-> hosts consistency -------------------------------------

    def test_peer_addresses_match_host_inventory(self):
        # Where a peer carries an in-allocation ipv6 and names a real host
        # (underscores normalised to hyphens), it must equal that host's
        # ansible_host — catches drift between the peers map and hosts.yml.
        for name, data in self.vars["peers"].items():
            host = name.replace("_", "-")
            v6 = data.get("ipv6")
            if not v6 or v6 in PLACEHOLDER_ADDRS:
                continue
            if _ip(v6) not in self.prefix:
                continue  # external peer (e.g. ns2) — not an inventory host addr
            if host not in self.host_addr:
                continue
            with self.subTest(peer=name):
                self.assertEqual(
                    v6,
                    self.host_addr[host],
                    f"peer {name} ipv6 {v6} != hosts.yml {host} {self.host_addr[host]}",
                )

    def test_router_loopbacks_live_in_the_loopback_subnet(self):
        for name in ("rtr", "cr1_nl1", "cr1_de1"):
            lo = self.vars["peers"][name].get("loopback")
            with self.subTest(peer=name):
                self.assertIsNotNone(lo, f"{name} has no loopback")
                self.assertIn(
                    _ip(lo),
                    self.loopbacks,
                    f"{name} loopback {lo} outside {self.loopbacks}",
                )

    def test_bgp_neighbors_are_external_global_addresses(self):
        # eBGP neighbor addresses must be valid, global, and NOT inside our own
        # allocation (a neighbor in as215932_prefix would be a config error).
        for name, addr in self.vars["bgp_peers"].items():
            with self.subTest(neighbor=name):
                ip = _ip(addr)
                self.assertTrue(ip.is_global, f"{name} {addr} is not a global address")
                self.assertNotIn(
                    ip,
                    self.prefix,
                    f"eBGP neighbor {name} {addr} is inside our own {self.prefix}",
                )

    def test_ssh_users_has_a_default(self):
        self.assertIn("default", self.vars["ssh_users"])


if __name__ == "__main__":
    unittest.main()
