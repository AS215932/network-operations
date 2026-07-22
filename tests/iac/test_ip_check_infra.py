import importlib.util
import ipaddress
import struct
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IPCheckInfrastructureTest(unittest.TestCase):
    def test_dns_observer_parser_is_probe_zone_scoped(self):
        observer = _load_module(
            "hyrule_dns_observer_test",
            REPO / "ansible/roles/knot/files/hyrule_dns_observer.py",
        )
        label = "0123456789abcdef0123456789abcdef"
        ipv4 = (
            f'2026-07-16T12:00:00Z AQ 198.51.100.20:53000 UDP 80b '
            f'"{label}.dns.check.hyrule.host." IN A'
        )
        ipv6 = (
            f'2026-07-16T12:00:00Z AQ [2001:4860:4860::8888]:53000 UDP 80b '
            f'"{label}.dns.check.hyrule.host." IN AAAA'
        )

        self.assertEqual(
            observer.parse_dnstap_line(ipv4, "dns.check.hyrule.host"),
            (label, "198.51.100.20", f"{label}.dns.check.hyrule.host."),
        )
        self.assertEqual(
            observer.parse_dnstap_line(ipv6, "dns.check.hyrule.host"),
            (label, "2001:4860:4860::8888", f"{label}.dns.check.hyrule.host."),
        )
        self.assertIsNone(
            observer.parse_dnstap_line(
                ipv4.replace("dns.check.hyrule.host", "hyrule.host"),
                "dns.check.hyrule.host",
            )
        )
        self.assertIsNone(
            observer.parse_dnstap_line(ipv4.replace(label, "short"), "dns.check.hyrule.host")
        )
        self.assertIsNone(
            observer.parse_dnstap_line(ipv4.replace(" AQ ", " AR "), "dns.check.hyrule.host")
        )

    def test_stun_monitor_parses_xor_mapped_address(self):
        checker = _load_module(
            "check_stun_binding_test",
            REPO / "configs/mon/icinga2/scripts/check_stun_binding.py",
        )
        transaction = bytes.fromhex("00112233445566778899aabb")
        address = ipaddress.IPv4Address("203.0.113.7").packed
        mask = struct.pack("!I", checker.COOKIE)
        encoded = bytes(left ^ right for left, right in zip(address, mask, strict=True))
        value = b"\x00\x01" + b"\x00\x00" + encoded
        attribute = struct.pack("!HH", 0x0020, len(value)) + value
        response = (
            struct.pack("!HHI", 0x0101, len(attribute), checker.COOKIE)
            + transaction
            + attribute
        )

        self.assertEqual(checker.mapped_address(response, transaction), "203.0.113.7")
        with self.assertRaises(ValueError):
            checker.mapped_address(response, b"x" * 12)

    def test_knot_capture_is_attached_only_to_dedicated_zone(self):
        group_vars = yaml.safe_load(
            (REPO / "ansible/inventory/group_vars/nameservers.yml").read_text()
        )
        zones = [zone["name"] for zone in group_vars["knot_zones"]]
        self.assertIn("dns.check.hyrule.host", zones)
        self.assertTrue(group_vars["knot_ip_check_observer_enabled"])

        template = (REPO / "ansible/roles/knot/templates/knot.conf.j2").read_text()
        self.assertIn("module: mod-dnstap/ip-check", template)
        self.assertIn("z.name == knot_ip_check_observer_zone", template)
        self.assertNotIn("template:\n  - id: default\n    module: mod-dnstap", template)

        parent = (REPO / "configs/hyrule.host.zone").read_text()
        probe = (REPO / "configs/dns.check.hyrule.host.zone").read_text()
        self.assertIn("dns.check IN  NS  ns1.servify.network.", parent)
        self.assertIn("$ORIGIN dns.check.hyrule.host.", probe)
        self.assertNotRegex(probe, r"(?m)^[0-9a-f]{32}\s+IN")

        template_path = REPO / "ansible/roles/knot/templates/knot.conf.j2"
        environment = Environment(
            loader=FileSystemLoader(str(template_path.parent)), undefined=StrictUndefined
        )
        environment.filters["bool"] = bool
        context = {
            "inventory_hostname": "dns",
            "knot_role": "primary",
            "knot_ip_check_observer_enabled": True,
            "knot_ip_check_observer_zone": "dns.check.hyrule.host",
            "knot_ip_check_observer_socket": "/run/hyrule-dns-observer/dnstap.sock",
            "knot_tsig_key_name": "hyrule-dns",
            "knot_tsig_secret": "test-secret",
            "knot_secondaries": [
                {"name": "ns2", "address_v4": "192.0.2.2", "address_v6": "2001:db8::2"}
            ],
            "peers": {
                "api": {"ipv6": "2001:db8::20"},
                "proxy": {"ipv6": "2001:db8::40"},
                "irc": {"ipv6": "2001:db8::80"},
            },
            "ops_prefix_v4": "192.0.2.0/24",
            "ops_prefix_v6": "2001:db8:1::/48",
            "knot_customer_dnssec_policy": "customer-domains",
            "knot_customer_catalog_enabled": False,
            "knot_customer_member_template": "customer-member",
            "knot_customer_zones_dir": "/var/lib/knot/customer-zones",
            "knot_customer_catalog_zone": "customer-zones.catalog.invalid",
            "knot_customer_zones_config": "/var/lib/knot/customer-zones.conf",
            "knot_zones": [{"name": name} for name in zones],
        }
        rendered = environment.get_template(template_path.name).render(**context)
        self.assertEqual(rendered.count("module: mod-dnstap/ip-check"), 1)
        self.assertIn("- domain: dns.check.hyrule.host\n", rendered)

    def test_coturn_is_binding_only_and_actively_monitored(self):
        template_path = REPO / "ansible/roles/ip_check_observer/templates/turnserver.conf.j2"
        rendered = Environment(
            loader=FileSystemLoader(str(template_path.parent)), undefined=StrictUndefined
        ).get_template(template_path.name).render(
            ip_check_observer_listening_port=3478,
            ip_check_observer_listening_ipv4="10.0.2.40",
            ip_check_observer_listening_ipv6="2a0c:b641:b50:2::40",
            ip_check_observer_realm="stun.hyrule.host",
        )
        directives = {
            line.strip()
            for line in rendered.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertTrue({"stun-only", "no-auth", "no-tls", "no-dtls", "no-cli"} <= directives)
        self.assertFalse(any(line.startswith(("user=", "relay-ip=", "lt-cred-mech")) for line in directives))
        self.assertNotIn("log-binding", directives)

        icinga = (REPO / "configs/mon/icinga2/services/ip-check-observer.conf").read_text()
        self.assertIn('object CheckCommand "stun_binding"', icinga)
        self.assertIn('apply Service "ip-check-stun-ipv4"', icinga)
        self.assertIn('apply Service "ip-check-stun-ipv6"', icinga)

    def test_family_probes_are_narrow_and_replace_forwarding_headers(self):
        zone = (REPO / "configs/hyrule.host.zone").read_text()
        self.assertRegex(zone, r"(?m)^v4\.check\s+IN\s+A\s+46\.105\.40\.223$")
        self.assertNotRegex(zone, r"(?m)^v4\.check\s+IN\s+AAAA\b")
        self.assertRegex(zone, r"(?m)^v6\.check\s+IN\s+AAAA\s+2a0c:b641:b50:2::40$")
        self.assertNotRegex(zone, r"(?m)^v6\.check\s+IN\s+A\b")

        caddy = (REPO / "configs/Caddyfile.j2").read_text()
        self.assertIn("v4.check.hyrule.host, v6.check.hyrule.host", caddy)
        self.assertIn("^/v1/ip-check/sessions/[^/]+/observe/http$", caddy)
        self.assertIn("header_up X-Forwarded-For {remote_host}", caddy)
        self.assertIn('Access-Control-Allow-Origin "https://hyrule.host"', caddy)
        self.assertIn('Cache-Control "no-store"', caddy)

    def test_firewall_and_dark_launch_contracts(self):
        rtr = (REPO / "ansible/generated/rtr/nftables.conf").read_text()
        proxy = (REPO / "ansible/generated/proxy/nftables.conf").read_text()
        self.assertIn("udp dport 3478 dnat to $PROXY_IP", rtr)
        self.assertIn("tcp dport 3478 dnat to $PROXY_IP", rtr)
        self.assertIn('udp dport 3478 counter accept comment "STUN binding observer"', proxy)

        cloud_env = (
            REPO / "ansible/roles/vault_agent/templates/hyrule-cloud.env.ctmpl.j2"
        ).read_text()
        self.assertIn('IP_QUALITY_ENABLED={{ or .Data.data.ip_quality_enabled "false" }}', cloud_env)
        self.assertIn('HYRULE_IP_QUALITY_TOOL_ENABLED={{ or .Data.data.ip_quality_tool_enabled "false" }}', cloud_env)
        self.assertIn('IP_CHECK_ENABLED={{ or .Data.data.ip_check_enabled "false" }}', cloud_env)
        self.assertIn("IP_CHECK_DNS_OBSERVER_SECRET={{ .Data.data.ip_check_dns_observer_secret }}", cloud_env)
        self.assertNotIn("?key={{", cloud_env)
        self.assertIn("HYRULE_WEB_ENABLE_IP_CHECK=false", (REPO / "configs/hyrule-web.env.j2").read_text())

        workflow = (REPO / ".github/workflows/apply.yml").read_text()
        self.assertIn("- ip-check-observer", workflow)
        self.assertTrue((REPO / "ansible/playbooks/ip-check-observer.yml").exists())
        observer_apply = (
            REPO / "ansible/roles/ip_check_observer/tasks/apply.yml"
        ).read_text()
        self.assertIn("Validate candidate Caddy configuration", observer_apply)
        self.assertIn("Promote validated Caddy configuration", observer_apply)


if __name__ == "__main__":
    unittest.main()
