"""Freshness gate: docs/network-flows.md must match the renderer's output.

The doc is a build artifact of scripts/render-network-flows.py reading the
structured Ansible inventory (host_meta, firewall_extra_rules,
network_flows_outbound, and network_flows.yml). If the committed doc drifts
from the structured data, this test fails — re-run the renderer and commit.
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RENDERER = REPO / "scripts" / "render-network-flows.py"
DOC = REPO / "docs" / "network-flows.md"


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_network_flows", RENDERER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rnf = _load_renderer()


class NetworkFlowsRenderTest(unittest.TestCase):
    def _render_to_temp(self) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "network-flows.md"
            proc = subprocess.run(
                [sys.executable, str(RENDERER), "--output", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"renderer failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
            )
            return out.read_text()

    def test_committed_doc_matches_render(self):
        self.assertTrue(DOC.exists(), "docs/network-flows.md is missing")
        rendered = self._render_to_temp()
        committed = DOC.read_text()
        self.assertEqual(
            committed,
            rendered,
            msg=(
                "docs/network-flows.md is stale. Regenerate it with "
                "`python3 scripts/render-network-flows.py` and commit the result."
            ),
        )

    def test_render_is_deterministic(self):
        first = self._render_to_temp()
        second = self._render_to_temp()
        self.assertEqual(first, second, "renderer is not deterministic")

    def test_committed_doc_carries_except_scopes(self):
        """The over-broad `all` flows must render their firewall exceptions."""
        doc = DOC.read_text()
        self.assertIn("all (except extmon, ns2, dom0) | rtr", doc)  # DNS recursion
        self.assertIn("all (except extmon, dom0) | tcp | 9100", doc)  # node_exporter
        self.assertIn("all (except mail) | tcp | 22", doc)  # noc SSH

    def test_committed_doc_carries_router_bgp_wg_inbound(self):
        """Router BGP/WG inbound (pf_bgp_peers/pf_wg_ports) must be rendered."""
        doc = DOC.read_text()
        self.assertIn("179 | External BGP (pf_bgp_peers)", doc)
        self.assertIn("WireGuard underlay tunnels (pf_wg_ports)", doc)


class ExceptValidationTest(unittest.TestCase):
    HOSTS = frozenset({"mon", "mail", "extmon", "dom0", "ns2", "rtr"})

    def _resolver(self):
        return rnf.Resolver({"peers": {"mon": {"ipv6": "::1"}, "rtr": {"ipv6": "::2"}}}, {})

    def test_except_token_must_be_a_host(self):
        flow = {"from": "all", "to": "rtr", "proto": "tcp", "port": 53, "except": ["bogus"]}
        with self.assertRaises(ValueError):
            rnf.render_cross_cutting_table(self._resolver(), [flow], valid_hosts=self.HOSTS)

    def test_except_requires_an_all_endpoint(self):
        flow = {"from": "mon", "to": "rtr", "proto": "tcp", "port": 53, "except": ["mail"]}
        with self.assertRaises(ValueError):
            rnf.render_cross_cutting_table(self._resolver(), [flow], valid_hosts=self.HOSTS)


class RouterInboundSynthesisTest(unittest.TestCase):
    def test_bgp_and_wg_rendered_placeholders_skipped(self):
        hv = {
            "pf_bgp_peers": ["2a0c:b640:10::ffff", "[LocIX placeholder]"],
            "pf_wg_ports": [1337, 1340],
        }
        rules = rnf.synthesize_router_inbound(hv)
        bgp = [r for r in rules if r["dport"] == 179]
        wg = [r for r in rules if r["proto"] == "udp"]
        self.assertEqual([r["src"] for r in bgp], ["2a0c:b640:10::ffff"])  # placeholder skipped
        self.assertEqual(wg[0]["dport"], [1337, 1340])

    def test_non_router_host_synthesizes_nothing(self):
        self.assertEqual(rnf.synthesize_router_inbound({"firewall_extra_rules": []}), [])


if __name__ == "__main__":
    unittest.main()
