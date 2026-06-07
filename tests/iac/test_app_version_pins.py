import re
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class ProductionAppVersionPinsTest(unittest.TestCase):
    def test_production_app_versions_are_full_commit_shas(self):
        cases = {
            "ansible/inventory/host_vars/noc.yml": ("noc_agent_version", "hyrule_mcp_version"),
            "ansible/inventory/host_vars/api.yml": ("hyrule_cloud_version",),
            "ansible/inventory/host_vars/web.yml": ("hyrule_web_version",),
        }

        for rel_path, keys in cases.items():
            with self.subTest(path=rel_path):
                values = yaml.safe_load((REPO / rel_path).read_text()) or {}
                for key in keys:
                    value = str(values.get(key, ""))
                    self.assertRegex(
                        value,
                        SHA_RE,
                        f"{rel_path}:{key} must be pinned to a 40-character commit SHA",
                    )
                    self.assertNotIn(value, {"main", "master", "HEAD"})


if __name__ == "__main__":
    unittest.main()
