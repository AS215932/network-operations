import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class AppPromotionBotTest(unittest.TestCase):
    def test_promote_apps_accepts_repository_dispatch(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/promote-apps.yml").read_text())

        triggers = workflow[True]
        self.assertIn("repository_dispatch", triggers)
        self.assertEqual(triggers["repository_dispatch"]["types"], ["app-promote"])

    def test_promote_apps_maps_known_repositories_to_pin_inputs(self):
        workflow_text = (REPO / ".github/workflows/promote-apps.yml").read_text()

        expected_cases = {
            "AS215932/noc-agent)": 'noc_agent_sha="$sha"',
            "AS215932/hyrule-mcp)": 'hyrule_mcp_sha="$sha"',
            "AS215932/hyrule-cloud)": 'hyrule_cloud_sha="$sha"',
            "AS215932/hyrule-web)": 'hyrule_web_sha="$sha"',
        }
        for repo_case, pin_assignment in expected_cases.items():
            with self.subTest(repo_case=repo_case):
                self.assertIn(repo_case, workflow_text)
                self.assertIn(pin_assignment, workflow_text)

        self.assertIn("unsupported promotion source repository", workflow_text)
        self.assertIn('gh api "repos/${repo}/commits/${sha}" --jq .sha', workflow_text)
        self.assertIn("repository_dispatch payload sha must be a 40-character commit SHA", workflow_text)
