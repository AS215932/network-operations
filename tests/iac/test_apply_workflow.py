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


if __name__ == "__main__":
    unittest.main()
