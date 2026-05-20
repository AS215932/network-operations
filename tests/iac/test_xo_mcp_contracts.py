import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class XoMcpContractsTest(unittest.TestCase):
    def test_streamable_http_gateway_is_stateful_and_bounded(self):
        defaults = yaml.safe_load((REPO / "ansible/roles/xo_mcp/defaults/main.yml").read_text())
        service = (REPO / "ansible/roles/xo_mcp/templates/xo-mcp.service.j2").read_text()

        self.assertEqual(defaults["xo_mcp_session_timeout_ms"], 120000)
        self.assertEqual(defaults["xo_mcp_tasks_max"], 256)
        self.assertEqual(defaults["xo_mcp_memory_max"], "1536M")
        self.assertIn("--stateful", service)
        self.assertRegex(service, r"--sessionTimeout\s+{{\s*xo_mcp_session_timeout_ms\s*}}")
        self.assertIn("KillMode=control-group", service)
        self.assertIn("TasksMax={{ xo_mcp_tasks_max }}", service)
        self.assertIn("MemoryMax={{ xo_mcp_memory_max }}", service)


if __name__ == "__main__":
    unittest.main()
