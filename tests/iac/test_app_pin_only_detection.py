import importlib.util
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("pin_only", REPO / "scripts/ci/is-app-pin-only.py")
pin_only = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pin_only)

SHA_A = "9c8156fd3cb97681a0db3535977cf6b6a2f1eadd"
SHA_B = "ee5018f6e4f55ef44ecba4c5609a6f8bfe8910d7"


def hunk(path, *lines):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n" + "\n".join(lines) + "\n"


class PinOnlyDetectionTest(unittest.TestCase):
    def test_promotion_diff_is_pin_only(self):
        files = ["ansible/inventory/host_vars/api.yml", "ansible/inventory/host_vars/web.yml"]
        diff = hunk(files[0], f"-hyrule_cloud_version: {SHA_A}", f"+hyrule_cloud_version: {SHA_B}") + hunk(
            files[1], f"-hyrule_web_version: {SHA_A}", f"+hyrule_web_version: {SHA_B}"
        )
        self.assertTrue(pin_only.is_pin_only(files, diff))

    def test_other_host_var_change_is_not_pin_only(self):
        path = "ansible/inventory/host_vars/api.yml"
        diff = hunk(path, f"+hyrule_cloud_version: {SHA_B}", "+hyrule_cloud_workers: 4")
        self.assertFalse(pin_only.is_pin_only([path], diff))

    def test_non_sha_version_is_not_pin_only(self):
        path = "ansible/inventory/host_vars/rtr.yml"
        self.assertFalse(pin_only.is_pin_only([path], hunk(path, "-frr_version: 9.1", "+frr_version: 10.2")))

    def test_file_outside_host_vars_is_not_pin_only(self):
        files = ["ansible/inventory/host_vars/api.yml", "configs/rtr/frr.conf"]
        diff = hunk(files[0], f"+hyrule_cloud_version: {SHA_B}")
        self.assertFalse(pin_only.is_pin_only(files, diff))

    def test_empty_diff_is_not_pin_only(self):
        self.assertFalse(pin_only.is_pin_only([], ""))

    def test_workflow_skips_heavy_tiers_but_keeps_static_on_pin_only(self):
        jobs = yaml.safe_load((REPO / ".github/workflows/iac-tests.yml").read_text())["jobs"]
        self.assertIn("pin_only", jobs["changes"]["outputs"])
        self.assertNotIn("pin_only", jobs["static-iac"]["if"])
        for name in ("ansible-idempotency", "batfish", "containerlab-frr"):
            self.assertIn("needs.changes.outputs.pin_only != 'true'", jobs[name]["if"], name)
        gate = jobs["iac-gate"]["steps"][0]
        self.assertIn("PIN_ONLY", gate["env"])


if __name__ == "__main__":
    unittest.main()
