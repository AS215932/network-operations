import configparser
import ipaddress
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "ansible/roles/networkd_resolved"


class PublicIPv4BackendContracts(unittest.TestCase):
    def test_rendered_backends_match_existing_dnat_and_return_gateway(self):
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        router = yaml.safe_load((REPO / "ansible/inventory/host_vars/rtr.yml").read_text())
        template = Environment(undefined=StrictUndefined).from_string(
            (ROLE / "templates/20-service-ipv4.conf.j2").read_text()
        )
        for host, destination in (("dns", "rtr_dns_v4"), ("proxy", "rtr_proxy_v4")):
            with self.subTest(host=host):
                variables = defaults | yaml.safe_load(
                    (REPO / f"ansible/inventory/host_vars/{host}.yml").read_text()
                )
                config = configparser.ConfigParser()
                config.read_string(template.render(**variables))
                address = ipaddress.ip_interface(config["Network"]["Address"])
                gateway = ipaddress.ip_address(config["Route"]["Gateway"])
                self.assertEqual(str(address.ip), router[destination])
                self.assertIn(gateway, address.network)
                self.assertNotEqual(gateway, address.ip)
                self.assertEqual(config["Route"]["Destination"], "0.0.0.0/0")
                # No clearing assignments or replacement of IPv6/DNS configuration.
                self.assertEqual(set(config["Network"]), {"address"})
                self.assertEqual(set(config["Route"]), {"destination", "gateway"})

    def test_only_existing_public_dns_and_proxy_opt_in(self):
        enabled = set()
        for path in (REPO / "ansible/inventory/host_vars").glob("*.yml"):
            variables = yaml.safe_load(path.read_text()) or {}
            if variables.get("networkd_service_ipv4_address"):
                enabled.add(path.stem)
        self.assertEqual(enabled, {"dns", "proxy"})
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        self.assertEqual(defaults["networkd_service_ipv4_address"], "")
