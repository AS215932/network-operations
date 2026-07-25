import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class ApplyWorkflowTest(unittest.TestCase):
    def test_apply_run_and_job_names_include_target(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/apply.yml").read_text())

        expected = "${{ inputs.dry_run == true && 'Dry-run' || 'Apply' }} playbook ${{ inputs.playbook }} to target(s) ${{ inputs.limit }}"
        self.assertEqual(workflow["run-name"], expected)
        self.assertEqual(workflow["jobs"]["apply"]["name"], expected)

    def test_empty_limit_excludes_staged_and_unprivileged_hosts(self):
        workflow = (REPO / ".github/workflows/apply.yml").read_text()
        safe_default = 'effective_limit="${LIMIT:-all:!ci-pr:!staged}"'

        self.assertEqual(workflow.count(safe_default), 2)
        self.assertIn('SEED_HOST_KEYS_LIMIT: ${{ inputs.limit != \'\' && inputs.limit', workflow)
        self.assertNotIn('if [ -n "$LIMIT" ]', workflow)

        seeder = (REPO / "scripts/ci/seed-missing-host-keys.sh").read_text()
        self.assertIn('inventory_limit="${SEED_HOST_KEYS_LIMIT:-all:!ci-pr:!staged}"', seeder)
        self.assertIn('ansible-inventory --list --limit "$inventory_limit"', seeder)


if __name__ == "__main__":
    unittest.main()
