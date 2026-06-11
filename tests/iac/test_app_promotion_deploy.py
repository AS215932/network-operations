import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class AppPromotionDeployTest(unittest.TestCase):
    def test_apply_matrix_is_serialized(self):
        workflow = yaml.safe_load(
            (REPO / ".github/workflows/app-promotion-deploy.yml").read_text()
        )

        strategy = workflow["jobs"]["apply"]["strategy"]
        self.assertEqual(strategy["fail-fast"], False)
        self.assertEqual(strategy["max-parallel"], 1)


if __name__ == "__main__":
    unittest.main()
