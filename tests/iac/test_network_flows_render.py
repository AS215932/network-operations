"""Freshness gate: docs/network-flows.md must match the renderer's output.

The doc is a build artifact of scripts/render-network-flows.py reading the
structured Ansible inventory (host_meta, firewall_extra_rules,
network_flows_outbound, and network_flows.yml). If the committed doc drifts
from the structured data, this test fails — re-run the renderer and commit.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RENDERER = REPO / "scripts" / "render-network-flows.py"
DOC = REPO / "docs" / "network-flows.md"


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


if __name__ == "__main__":
    unittest.main()
