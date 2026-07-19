import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


class GossValidationTest(unittest.TestCase):
    def test_cloud_target_installs_goss(self):
        defaults = yaml.safe_load(
            (REPO / "ansible/roles/hyrule_cloud/defaults/main.yml").read_text()
        )

        self.assertIn("goss", defaults["hyrule_cloud_packages"])

    def test_cloud_playbook_runs_goss_on_api_and_always_removes_spec(self):
        plays = yaml.safe_load(
            (REPO / "ansible/playbooks/goss_cloud.yml").read_text()
        )

        self.assertEqual(len(plays), 1)
        play = plays[0]
        self.assertEqual(play["hosts"], "api")
        self.assertTrue(play["become"])
        validation = play["tasks"][0]
        block = validation["block"]
        self.assertIn("ansible.builtin.copy", block[0])
        self.assertEqual(
            block[1]["ansible.builtin.command"]["argv"],
            [
                "goss",
                "-g",
                "{{ hyrule_cloud_goss_remote }}",
                "validate",
                "--format",
                "documentation",
            ],
        )
        cleanup = validation["always"][0]["ansible.builtin.file"]
        self.assertEqual(cleanup["path"], "{{ hyrule_cloud_goss_remote }}")
        self.assertEqual(cleanup["state"], "absent")

    def test_cloud_wrapper_invokes_target_playbook_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_ansible = tmp_path / "ansible-playbook"
            log_path = tmp_path / "ansible.json"
            fake_ansible.write_text(
                "#!/usr/bin/python3\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "Path(os.environ['FAKE_ANSIBLE_LOG']).write_text(json.dumps({\n"
                "    'args': sys.argv[1:], 'cwd': os.getcwd(),\n"
                "}))\n"
                "raise SystemExit(int(os.environ.get('FAKE_ANSIBLE_EXIT', '0')))\n"
            )
            fake_ansible.chmod(
                fake_ansible.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
            env = os.environ.copy()
            env["PATH"] = f"{tmp_path}:{env['PATH']}"
            env["FAKE_ANSIBLE_LOG"] = str(log_path)
            env["FAKE_ANSIBLE_EXIT"] = "23"

            result = subprocess.run(
                [str(REPO / "scripts/ci/goss-validate.sh"), "cloud", "api"],
                cwd=REPO,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 23)
            invocation = json.loads(log_path.read_text())
            self.assertEqual(invocation["cwd"], str(REPO / "ansible"))
            self.assertEqual(
                invocation["args"],
                [
                    "playbooks/goss_cloud.yml",
                    "-e",
                    "ansible_user=ci",
                    "--limit",
                    "api",
                ],
            )


if __name__ == "__main__":
    unittest.main()
