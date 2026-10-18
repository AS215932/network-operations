"""Contracts for the overlay VM (Tor non-exit relay / i2pd / Yggdrasil).

Guards the three properties Svag asked for on 2026-10-18 and that must not
regress silently:

* Tor is a NON-exit relay (no exit policy that accepts anything).
* Yggdrasil's ygg0 sits in its own nftables zone: no inbound services, no
  forwarding to or from the rest of AS215932, no multicast/listener, and the
  private key never lives in the repo.
* Access is the dedicated `kim` operator user only — no root keys.

Plus the rtr side: the Tor IPv4 ORPort DNAT must never collide with Jool's
NAT64 pool4 or rtr's ephemeral ports, and stays off until explicitly enabled.
"""
import json
import re
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "ansible/generated/overlay"
ROLE = REPO / "ansible/roles/overlay"
HOST_VARS = REPO / "ansible/inventory/host_vars"


def _yaml(path):
    return yaml.safe_load(Path(path).read_text())


def _directives(text):
    """torrc -> list of (keyword, value) ignoring comments/blank lines."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        out.append((key, value.strip()))
    return out


def _ranges(spec):
    lo, _, hi = spec.partition("-")
    return int(lo), int(hi or lo)


def _task_by_name(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(f"task not found: {name}")


class TorIsNonExitTest(unittest.TestCase):
    def setUp(self):
        self.torrc = (GEN / "torrc").read_text()
        self.directives = _directives(self.torrc)

    def test_rendered_torrc_is_a_non_exit_relay(self):
        d = self.directives
        self.assertIn(("ExitRelay", "0"), d)
        self.assertIn(("IPv6Exit", "0"), d)
        self.assertIn(("ExitPolicy", "reject *:*"), d)
        self.assertIn(("SocksPort", "0"), d)
        for key, value in d:
            if key in ("ExitPolicy", "ReducedExitPolicy"):
                self.assertNotIn("accept", value, f"{key} {value} would open an exit")
            self.assertNotEqual(key, "ReducedExitPolicy")
        self.assertNotIn(("ExitRelay", "1"), d)

    def test_template_hardcodes_the_exit_lockdown(self):
        # The lockdown lines must not be variable-driven: flipping a host var
        # must never be enough to turn overlay into an exit.
        template = (ROLE / "templates/torrc.j2").read_text()
        for line in ("ExitRelay 0", "IPv6Exit 0", "ExitPolicy reject *:*", "ExitPolicy reject6 *:*"):
            self.assertIn(f"\n{line}\n", template)

    def test_role_only_supports_relay_mode(self):
        defaults = _yaml(ROLE / "defaults/main.yml")
        host = _yaml(HOST_VARS / "overlay.yml")
        self.assertEqual(defaults["overlay_tor_mode"], "relay")
        self.assertEqual(host["overlay_tor_mode"], "relay")
        validate = _yaml(ROLE / "tasks/validate.yml")
        guard = _task_by_name(validate, "Tor must be a non-exit relay (exit needs a reviewed role change)")
        self.assertEqual(guard["ansible.builtin.assert"]["that"], ["overlay_tor_mode == 'relay'"])

    def test_tor_is_held_until_contactinfo_is_set(self):
        host = _yaml(HOST_VARS / "overlay.yml")
        validate = _yaml(ROLE / "tasks/validate.yml")
        guard = _task_by_name(validate, "Tor ContactInfo must be set before Tor is enabled")
        self.assertIn("'CHANGEME' not in overlay_tor_contact_info", guard["ansible.builtin.assert"]["that"])
        if "CHANGEME" in host["overlay_tor_contact_info"]:
            self.assertFalse(host["overlay_tor_enabled"], "Tor enabled with placeholder ContactInfo")

    def test_bandwidth_is_limited(self):
        d = dict(self.directives)
        self.assertIn("RelayBandwidthRate", d)
        self.assertIn("RelayBandwidthBurst", d)


class YggdrasilZoneIsolationTest(unittest.TestCase):
    def setUp(self):
        self.ygg_nft = (GEN / "overlay-ygg.nft").read_text()
        self.main_nft = (GEN / "nftables.conf").read_text()
        self.host = _yaml(HOST_VARS / "overlay.yml")
        self.defaults = _yaml(ROLE / "defaults/main.yml")
        self.ifname = self.defaults["overlay_yggdrasil_ifname"]

    def _chain(self, name):
        m = re.search(r"chain %s \{(.*?)\n    \}" % name, self.ygg_nft, re.S)
        self.assertIsNotNone(m, f"chain {name} missing in overlay-ygg.nft")
        return m.group(1)

    def test_no_forwarding_between_ygg0_and_the_as(self):
        fwd = self._chain("forward")
        self.assertIn(f'iifname "{self.ifname}" counter drop', fwd)
        self.assertIn(f'oifname "{self.ifname}" counter drop', fwd)
        self.assertIn("ip6 saddr 200::/7 counter drop", fwd)
        self.assertIn("ip6 daddr 200::/7 counter drop", fwd)
        self.assertNotRegex(fwd, r"\baccept\b(?!;)", "ygg forward chain must not accept anything")
        # Main table: default-drop forward with no extra accepts.
        main_fwd = re.search(r"chain forward \{(.*?)\n    \}", self.main_nft, re.S).group(1)
        self.assertIn("policy drop", main_fwd)
        self.assertEqual(
            [l.strip() for l in main_fwd.splitlines() if "accept" in l and "policy" not in l],
            ["ct state established,related accept"],
        )

    def test_ygg0_input_admits_only_replies_and_icmpv6(self):
        inp = self._chain("input")
        lines = [l.strip() for l in inp.splitlines() if l.strip().startswith(f'iifname "{self.ifname}"')]
        accepts = [l for l in lines if l.endswith("accept")]
        self.assertEqual(len(accepts), 2, accepts)
        self.assertTrue(any("ct state established,related accept" in l for l in accepts))
        self.assertTrue(any("icmpv6 type" in l for l in accepts))
        self.assertTrue(lines[-1].startswith(f'iifname "{self.ifname}" counter drop'), lines[-1])
        # The isolation chains run before table inet filter (priority filter).
        self.assertIn("priority filter - 10", inp)

    def test_public_service_rules_are_bound_to_the_infra_nic(self):
        for rule in self.host["firewall_extra_rules"]:
            if rule["src"] in ("any", "::/0"):
                self.assertEqual(rule.get("iifname"), "enX0", rule)
        # No rule in table inet filter references ygg0 (it would be an allow).
        filter_table = self.main_nft.split("table inet filter {", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn(self.ifname, filter_table)

    def test_firewall_reload_keeps_the_ygg_table(self):
        include_dir = self.host["firewall_nft_include_dir"]
        self.assertEqual(include_dir, self.defaults["overlay_nft_include_dir"])
        self.assertIn(f'include "{include_dir}/*.nft"', self.main_nft)
        self.assertLess(self.main_nft.index("flush ruleset"), self.main_nft.index("include"))
        self.assertTrue(self.defaults["overlay_nft_ygg_file"].startswith("{{ overlay_nft_include_dir }}/"))
        self.assertTrue(self.defaults["overlay_nft_ygg_file"].endswith(".nft"))
        # Other hosts are unaffected by the new hook.
        self.assertNotIn("include", (REPO / "ansible/generated/irc/nftables.conf").read_text())

    def test_ygg_table_is_loaded_before_yggdrasil_starts(self):
        tasks = _yaml(ROLE / "tasks/apply.yml")
        names = [t["name"] for t in tasks]
        self.assertLess(names.index("Load ygg0 isolation before any overlay daemon starts"),
                        names.index("Yggdrasil: service state"))
        self.assertLess(names.index("Refuse to start Yggdrasil before the firewall role carries the include"),
                        names.index("Yggdrasil: service state"))

    def test_yggdrasil_peers_outbound_only_without_multicast(self):
        conf = json.loads((GEN / "yggdrasil.conf").read_text())
        self.assertEqual(conf["MulticastInterfaces"], [])
        self.assertEqual(conf["Listen"], [])
        self.assertEqual(conf["IfName"], self.ifname)
        self.assertGreaterEqual(len(conf["Peers"]), 3)
        for peer in conf["Peers"]:
            self.assertTrue(peer.startswith("tls://"), peer)

    def test_yggdrasil_private_key_is_generated_on_host_never_in_repo(self):
        conf = json.loads((GEN / "yggdrasil.conf").read_text())
        self.assertNotIn("PrivateKey", conf)
        self.assertEqual(conf["PrivateKeyPath"], self.defaults["overlay_yggdrasil_key_path"])
        tasks = _yaml(ROLE / "tasks/apply.yml")
        gen = _task_by_name(tasks, "Yggdrasil: generate private key on-host if missing (never printed)")
        self.assertEqual(gen["args"]["creates"], "{{ overlay_yggdrasil_key_path }}")
        self.assertTrue(gen["no_log"])
        self.assertIn("openssl genpkey -algorithm ed25519", gen["ansible.builtin.shell"])
        for path in list(ROLE.rglob("*")) + [HOST_VARS / "overlay.yml"]:
            if path.is_file():
                self.assertNotIn("BEGIN PRIVATE KEY", path.read_text(), path)

    def test_overlay_never_forwards(self):
        tasks = _yaml(ROLE / "tasks/apply.yml")
        sysctl = _task_by_name(tasks, "Keep forwarding off (overlay is an endpoint, never a router)")
        content = sysctl["ansible.builtin.copy"]["content"]
        self.assertIn("net.ipv4.ip_forward = 0", content)
        self.assertIn("net.ipv6.conf.all.forwarding = 0", content)


class OverlayAccessTest(unittest.TestCase):
    def test_overlay_uses_the_dedicated_kim_user_not_root(self):
        inventory = _yaml(REPO / "ansible/inventory/hosts.yml")
        overlay = inventory["all"]["children"]["linux"]["hosts"]["overlay"]
        self.assertEqual(overlay["ansible_user"], "kim")
        all_vars = _yaml(REPO / "ansible/inventory/group_vars/all.yml")
        self.assertEqual(all_vars["ssh_users"]["overlay"], "kim")
        # kim-ops-key targets `linux`, so overlay gets the managed kim account.
        play = _yaml(REPO / "ansible/playbooks/kim-ops-key.yml")[0]
        self.assertIn("linux", play["hosts"])

    def test_create_script_seeds_no_root_or_svag_keys(self):
        script = (REPO / "scripts/create-overlay-vm.sh").read_text()
        self.assertIn("disable_root: true", script)
        self.assertIn("ssh_pwauth: false", script)
        self.assertIn("  - name: kim\n    shell: /bin/sh", script)
        self.assertNotIn("name: root", script)
        self.assertNotIn("name: svag", script)
        self.assertNotRegex(script, r"ssh-(ed25519|rsa) AAAA", "no hard-coded keys")
        self.assertEqual(script.count("ssh_authorized_keys:"), 1)


class RtrOverlayDnatTest(unittest.TestCase):
    def setUp(self):
        self.rtr = _yaml(HOST_VARS / "rtr.yml")
        self.host = _yaml(HOST_VARS / "overlay.yml")
        self.all = _yaml(REPO / "ansible/inventory/group_vars/all.yml")

    def _render_rtr(self, enabled):
        env = Environment(
            loader=FileSystemLoader(str(REPO / "ansible/roles/firewall/templates")),
            undefined=StrictUndefined,
        )
        # Ansible's `bool` filter, which plain Jinja lacks.
        env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        ctx = {
            "rtr_wan_ip": self.rtr["rtr_wan_ip"],
            "rtr_wan_if": self.rtr["rtr_wan_if"],
            "rtr_dns_v4": self.rtr["rtr_dns_v4"],
            "rtr_proxy_v4": self.rtr["rtr_proxy_v4"],
            "rtr_vpn_v4": self.rtr["rtr_vpn_v4"],
            "rtr_tunnel_v4": self.rtr["rtr_tunnel_v4"],
            "rtr_overlay_dnat_enabled": enabled,
            "rtr_overlay_v4": self.all["peers"]["overlay"]["infra_v4"],
            "rtr_overlay_tor_orport_public": self.rtr["rtr_overlay_tor_orport_public"],
            "rtr_overlay_tor_orport": self.rtr["rtr_overlay_tor_orport"],
            "customer_isolation_block_v4": ["10.0.0.0/24"],
            "customer_isolation_block_v6": ["2a0c:b641:b50:2::/64"],
            "customer_subnet": self.all["customer_subnet"],
            "firewall_input_policy": "drop",
            "firewall_log_only": False,
            "firewall_extra_rules": [],
            "firewall_extra_raw_nft": "",
            "ssh_allow_sources_v6": [],
            "ssh_allow_sources_v4": [],
        }
        return env.get_template("nftables-rtr.conf.j2").render(**ctx)

    def test_dnat_is_off_by_default_and_rtr_render_is_unchanged(self):
        self.assertFalse(self.rtr["rtr_overlay_dnat_enabled"])
        self.assertNotIn("OVERLAY_IP", (REPO / "ansible/generated/rtr/nftables.conf").read_text())

    def test_enabled_dnat_maps_public_port_to_overlay_orport(self):
        ruleset = self._render_rtr(True)
        public = self.rtr["rtr_overlay_tor_orport_public"]
        orport = self.rtr["rtr_overlay_tor_orport"]
        self.assertIn("define OVERLAY_IP = 10.0.2.71", ruleset)
        self.assertIn(f"ip daddr $WAN_IP tcp dport {public} dnat to $OVERLAY_IP:{orport}", ruleset)
        self.assertIn(f"ip daddr $OVERLAY_IP tcp dport {orport} accept", ruleset)
        self.assertNotIn("OVERLAY_IP", self._render_rtr(False))

    def test_ports_agree_between_rtr_overlay_and_torrc(self):
        self.assertEqual(self.rtr["rtr_overlay_tor_orport_public"], self.host["overlay_tor_orport_v4_public"])
        self.assertEqual(self.rtr["rtr_overlay_tor_orport"], self.host["overlay_tor_orport"])
        torrc = (GEN / "torrc").read_text()
        self.assertIn(f"ORPort {self.rtr['rtr_wan_ip']}:{self.host['overlay_tor_orport_v4_public']} NoListen", torrc)
        self.assertIn(f"ORPort 10.0.2.71:{self.host['overlay_tor_orport']} NoAdvertise", torrc)
        self.assertIn(f"ORPort [{self.all['peers']['overlay']['ipv6']}]:{self.host['overlay_tor_orport']}", torrc)

    def test_public_port_avoids_jool_pool4_ephemeral_and_other_dnat(self):
        public = self.rtr["rtr_overlay_tor_orport_public"]
        jool = json.loads((REPO / "configs/rtr/jool/jool.conf").read_text())
        for entry in jool["pool4"]:
            if entry["protocol"] == "TCP":
                lo, hi = _ranges(entry["port range"])
                self.assertFalse(lo <= public <= hi, f"{public} inside Jool TCP pool4 {lo}-{hi}")
        sysctl = (REPO / "configs/rtr/sysctl.conf").read_text()
        lo, hi = map(int, re.search(r"ip_local_port_range=(\d+)\s+(\d+)", sysctl).groups())
        self.assertFalse(lo <= public <= hi, f"{public} inside rtr ephemeral range")
        ruleset = self._render_rtr(True)
        tcp_dnat = re.findall(r"tcp dport (\{[^}]*\}|\S+) dnat", ruleset)
        claimed = []
        for spec in tcp_dnat:
            for part in spec.strip("{} ").split(","):
                claimed.append(_ranges(part.strip()))
        hits = [r for r in claimed if r[0] <= public <= r[1]]
        self.assertEqual(len(hits), 1, f"public ORPort {public} claimed by {hits}")


class OverlayInventoryTest(unittest.TestCase):
    def test_address_is_unique_on_the_infra_segment(self):
        all_vars = _yaml(REPO / "ansible/inventory/group_vars/all.yml")
        mine = all_vars["peers"]["overlay"]["ipv6"]
        self.assertEqual(mine, "2a0c:b641:b50:2::71")
        others = [d.get("ipv6") for n, d in all_vars["peers"].items() if n != "overlay"]
        self.assertNotIn(mine, others)
        # ::60 was vpn's infra address and is still referenced by WG clients.
        self.assertNotEqual(mine, "2a0c:b641:b50:2::60")

    def test_drift_sweep_skips_overlay_until_it_is_built(self):
        text = (REPO / "scripts/ci/check-drift.sh").read_text()
        self.assertIn('all:!ci-pr:!rtr-bsd:!overlay', text)


if __name__ == "__main__":
    unittest.main()
