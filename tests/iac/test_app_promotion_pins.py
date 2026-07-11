import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def load_script(name: str):
    path = REPO / "scripts/ci" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AppPromotionPinsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.promote = load_script("promote-app-pins.py")
        cls.pending = load_script("pending-app-promotions.py")

    def test_multi_runtime_repositories_share_one_promotion_flag(self):
        flags = self.promote.PROMOTION_FLAGS
        self.assertEqual(flags["knowledge_mcp_version"], "--knowledge-sha")
        self.assertEqual(flags["knowledge_loop_version"], "--knowledge-sha")
        self.assertEqual(flags["knowledge_api_version"], "--knowledge-sha")
        self.assertEqual(flags["agent_core_collector_version"], "--agent-core-sha")
        self.assertEqual(flags["agent_core_coordinator_version"], "--agent-core-sha")

    def test_first_promotion_replaces_a_quoted_moving_scaffold(self):
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.yml"
            path.write_text('agent_core_coordinator_version: "main"\n')
            old = self.promote.update_pin(path, "agent_core_coordinator_version", sha)

            self.assertEqual(old, "main")
            self.assertEqual(
                path.read_text(), f"agent_core_coordinator_version: {sha}\n"
            )

    def test_pending_reader_preserves_a_first_promotion_baseline(self):
        self.assertEqual(
            self.pending.extract_pin(
                'soc_agent_version: "main"\n', "soc_agent_version"
            ),
            "main",
        )

    def test_first_promotion_body_links_the_immutable_commit(self):
        sha = "b" * 40
        body = self.promote.render_body(
            "First SOC promotion",
            "Dark scaffold only.",
            [
                (
                    "soc_agent_version",
                    "AS215932/soc-agent",
                    "soc",
                    "main",
                    sha,
                )
            ],
        )
        self.assertIn(f"https://github.com/AS215932/soc-agent/commit/{sha}", body)
        self.assertIn("disable the affected service", body)


if __name__ == "__main__":
    unittest.main()
