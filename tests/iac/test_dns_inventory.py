import ipaddress
import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
FORWARD_ZONE = REPO / "configs/as215932.net.zone"
REVERSE_ZONE = REPO / "configs/0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa.zone"
GROUP_VARS = REPO / "ansible/inventory/group_vars/all.yml"
AS215932_REVERSE_ORIGIN = "0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa."


def parse_zone_records(path):
    origin = ""
    records = []
    for raw in path.read_text().splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("$ORIGIN"):
            origin = line.split()[1].rstrip(".") + "."
            continue
        if line.startswith("$"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 4:
            continue
        name = parts[0]
        if parts[1].upper() == "IN":
            rtype = parts[2].upper()
            value = parts[3]
        elif len(parts) >= 5 and parts[2].upper() == "IN":
            rtype = parts[3].upper()
            value = parts[4]
        else:
            continue
        if name == "@":
            fqdn = origin
        elif name.endswith("."):
            fqdn = name
        else:
            fqdn = f"{name}.{origin}"
        records.append((fqdn, rtype, value.rstrip(".")))
    return records


def reverse_origin_to_prefix_nibbles(origin):
    labels = origin.rstrip(".").split(".")
    reverse_labels = labels[: labels.index("ip6")]
    return "".join(reversed(reverse_labels))


def ptr_relative_to_ip(relative_name, origin):
    prefix = reverse_origin_to_prefix_nibbles(origin)
    relative = relative_name.removesuffix(origin).rstrip(".")
    suffix = "".join(reversed(relative.split(".")))
    nibbles = (prefix + suffix).ljust(32, "0")
    return ipaddress.IPv6Address(int(nibbles, 16))


class DnsInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forward_records = parse_zone_records(FORWARD_ZONE)
        cls.reverse_records = parse_zone_records(REVERSE_ZONE)
        cls.forward_by_name = {}
        for name, rtype, value in cls.forward_records:
            if rtype == "AAAA":
                cls.forward_by_name.setdefault(name.rstrip("."), set()).add(ipaddress.IPv6Address(value))
        cls.forward_ips = {ip for values in cls.forward_by_name.values() for ip in values}

        cls.ptr_by_ip = {}
        for name, rtype, value in cls.reverse_records:
            if rtype != "PTR":
                continue
            ip = ptr_relative_to_ip(name, AS215932_REVERSE_ORIGIN)
            cls.ptr_by_ip.setdefault(ip, set()).add(value.rstrip("."))

        cls.vars = yaml.safe_load(GROUP_VARS.read_text())

    def test_ci_forward_and_reverse_exist(self):
        ci_ip = ipaddress.IPv6Address("2a0c:b641:b50:2::d0")
        self.assertIn(ci_ip, self.forward_by_name["ci.as215932.net"])
        self.assertIn("ci.as215932.net", self.ptr_by_ip[ci_ip])

    def test_peer_addresses_have_forward_and_reverse_dns(self):
        infra_net = ipaddress.IPv6Network(self.vars["infra_subnet"])
        expected = {}
        for name, data in self.vars["peers"].items():
            dns_name = name.replace("_", "-")
            if data.get("ipv6") and data["ipv6"] != "::":
                ip = ipaddress.IPv6Address(data["ipv6"])
                if ip in infra_net:
                    expected[dns_name] = ip
            elif name.startswith("cr1_") and data.get("loopback"):
                expected[dns_name] = ipaddress.IPv6Address(data["loopback"])

        for name, ip in expected.items():
            with self.subTest(name=name):
                self.assertIn(ip, self.forward_ips, f"{name} has no forward AAAA for {ip}")
                self.assertIn(ip, self.ptr_by_ip, f"{name} has no PTR for {ip}")

    def test_all_as215932_aaaa_records_have_matching_ptr(self):
        reverse_net = ipaddress.IPv6Network("2a0c:b641:b50::/48")
        for fqdn, ips in self.forward_by_name.items():
            for ip in ips:
                if ip not in reverse_net:
                    continue
                with self.subTest(fqdn=fqdn, ip=str(ip)):
                    self.assertIn(ip, self.ptr_by_ip)
                    ptr_targets = self.ptr_by_ip[ip]
                    self.assertTrue(
                        any(ip in self.forward_by_name.get(target, set()) for target in ptr_targets),
                        f"PTR targets {sorted(ptr_targets)} do not resolve back to {ip}",
                    )

    def test_public_zones_do_not_publish_private_or_special_addresses(self):
        for zone in (REPO / "configs").glob("*.zone"):
            for fqdn, rtype, value in parse_zone_records(zone):
                if rtype == "A":
                    ip = ipaddress.IPv4Address(value)
                    self.assertFalse(ip.is_private, f"{zone}: {fqdn} leaks private IPv4 {ip}")
                if rtype == "AAAA":
                    ip6 = ipaddress.IPv6Address(value)
                    self.assertFalse(ip6.is_link_local, f"{zone}: {fqdn} leaks link-local IPv6 {ip6}")
                    self.assertFalse(ip6.is_private and not ip6.is_global, f"{zone}: {fqdn} leaks ULA/special IPv6 {ip6}")


if __name__ == "__main__":
    unittest.main()
