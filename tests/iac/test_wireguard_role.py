"""wireguard role contracts (issue #14): keys stay on-host, no route leaks."""
import re
import unittest
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "ansible/roles/wireguard"
TEMPLATE = (ROLE / "templates/wg.conf.j2").read_text()


def render(host, link):
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=False)
    env.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
    env.filters["regex_replace"] = lambda value, pattern, repl: re.sub(pattern, repl, value)
    lookup = lambda kind, path: Path(path).read_text()
    return env.from_string(TEMPLATE).render(
        lookup=lookup,
        ansible_managed="managed",
        inventory_hostname=host,
        wireguard_config_src_dir=str(REPO / "configs"),
        wireguard_conf_dir="/usr/local/etc/wireguard",
        item=link,
    )


def links(host):
    return yaml.safe_load((REPO / f"ansible/inventory/host_vars/{host}.yml").read_text()).get("wireguard_links", [])


class WireguardRoleTest(unittest.TestCase):
    HOSTS = {"cr1-nl1": "wg5", "cr1-de1": "wg3", "cr1-ch1": "wg3"}

    def test_rtr_bsd_links_are_declared(self):
        for host, iface in self.HOSTS.items():
            self.assertEqual([l["name"] for l in links(host)], [iface], host)

    def test_render_has_no_private_key_and_no_routes(self):
        for host in self.HOSTS:
            for link in links(host):
                out = render(host, link)
                with self.subTest(host=host):
                    self.assertNotRegex(out, r"(?m)^\s*PrivateKey")
                    self.assertIn("Table = off", out)
                    self.assertIn(f"PostUp = wg set %i private-key /usr/local/etc/wireguard/{link['name']}.key", out)
                    self.assertIn(f"Address = {link['link_local']}", out)
                    self.assertNotRegex(out, r"(?m)^\s*;")
                    self.assertNotIn("<", out.split("[Peer]", 1)[1])

    def test_key_generation_is_no_log_and_idempotent(self):
        tasks = yaml.safe_load((ROLE / "tasks/apply.yml").read_text())
        gen = next(t for t in tasks if "generate private key" in t["name"])
        self.assertTrue(gen["no_log"])
        self.assertIn("creates", gen["args"])
        self.assertIn("umask 077", gen["shell"])

    def test_apply_is_gated(self):
        main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        apply = next(t for t in main if t.get("include_tasks") == "apply.yml")
        self.assertEqual(apply["when"], "wireguard_apply | bool")


if __name__ == "__main__":
    unittest.main()
