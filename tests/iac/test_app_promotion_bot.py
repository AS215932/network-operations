import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


def workflow_triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True, {}))


class AppPromotionBotTest(unittest.TestCase):
    def test_promote_apps_accepts_repository_dispatch(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/promote-apps.yml").read_text())

        triggers = workflow_triggers(workflow)
        self.assertIn("repository_dispatch", triggers)
        self.assertEqual(triggers["repository_dispatch"]["types"], ["app-promote"])

    def test_promote_apps_maps_known_repositories_to_pin_inputs(self):
        workflow_text = (REPO / ".github/workflows/promote-apps.yml").read_text()

        self.assertIn("ref: ${{ github.sha }}", workflow_text)

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
        self.assertIn('gh api "repos/${repo}/git/commits/${sha}" --jq .sha', workflow_text)
        self.assertIn("repository_dispatch payload sha must be a 40-character commit SHA", workflow_text)

    def test_promote_apps_retries_and_falls_back_to_public_commit_verification(self):
        workflow_text = (REPO / ".github/workflows/promote-apps.yml").read_text()

        self.assertIn("for attempt in 1 2 3", workflow_text)
        self.assertIn("Authenticated source commit lookup failed", workflow_text)
        self.assertIn("Falling back to public source commit verification", workflow_text)
        self.assertIn("--retry-all-errors", workflow_text)
        self.assertIn(
            '"https://api.github.com/repos/${repo}/git/commits/${sha}"',
            workflow_text,
        )

    def test_promote_apps_uses_app_token_and_rebuilds_branch_from_main(self):
        workflow_text = (REPO / ".github/workflows/promote-apps.yml").read_text()

        self.assertIn("actions/create-github-app-token@v2", workflow_text)
        self.assertIn("GH_TOKEN: ${{ steps.app-token.outputs.token }}", workflow_text)
        # The branch must be rebuilt from origin/main every run — never
        # continued from its old tip (once main moves a pin via a manually
        # merged deploy PR, a never-rebased branch wedges into permanent
        # merge conflict, PR #316) and never based on github.sha (a
        # workflow_dispatch from a feature ref must not publish that ref's
        # commits to the promotion branch).
        self.assertIn('git checkout -B "$BRANCH" origin/main', workflow_text)
        self.assertNotIn('git checkout -B "$BRANCH" "origin/$BRANCH"', workflow_text)
        # Still-pending pins from the old tip are carried forward only when
        # the app repo confirms they are ahead of main's value.
        self.assertIn("scripts/ci/pending-app-promotions.py", workflow_text)
        self.assertIn("Carry forward pending app promotions", workflow_text)
        self.assertIn('git push --force-with-lease origin "$BRANCH"', workflow_text)
        # The PR body is rendered from the full branch-vs-main pin delta so it
        # also covers carried-forward pins and main-relative rollback SHAs.
        self.assertIn("--body-from-ref origin/main", workflow_text)
