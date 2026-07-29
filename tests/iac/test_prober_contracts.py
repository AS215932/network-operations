import unittest
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "ansible/roles/hyrule_prober"
SERVICE_USER = "{{ hyrule_prober_user }}"


class ProberContractsTest(unittest.TestCase):
    def setUp(self):
        self.tasks = yaml.safe_load((ROLE / "tasks/apply.yml").read_text())
        self.defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())

    def _task(self, name):
        for task in self.tasks:
            if task.get("name") == name:
                return task
        self.fail(f"hyrule_prober apply.yml has no task named {name!r}")

    def test_venv_lives_inside_the_root_owned_install_dir(self):
        # The premise of the next test: the venv sits under an install dir that
        # is deliberately root-owned, so it cannot inherit write access and has
        # to be created explicitly for the service user.
        install_dir = self.defaults["hyrule_prober_install_dir"]
        venv_dir = self.defaults["hyrule_prober_venv_dir"]
        self.assertTrue(
            venv_dir.startswith(install_dir + "/"),
            f"{venv_dir} is no longer under {install_dir}; revisit the ownership contract",
        )

        dirs = {entry["path"]: entry for entry in self._task("Ensure runtime directories exist")["loop"]}
        self.assertEqual(
            dirs["{{ hyrule_prober_install_dir }}"].get("owner"),
            "root",
            "install dir is expected to stay root-owned",
        )

    def test_venv_directory_is_precreated_for_the_service_user(self):
        # `python3 -m venv` and the editable pip install both run as
        # hyrule_prober_user. With the venv path only ever created implicitly,
        # the very first apply died on:
        #   Error: [Errno 13] Permission denied: '/opt/hyrule-prober/.venv'
        # because /opt/hyrule-prober is root-owned 0755. Pre-create it owned by
        # the service user, exactly as src/ already is.
        dirs = {entry["path"]: entry for entry in self._task("Ensure runtime directories exist")["loop"]}

        self.assertIn(
            "{{ hyrule_prober_venv_dir }}",
            dirs,
            "the venv dir must be pre-created; the service user cannot mkdir it "
            "inside the root-owned install dir",
        )
        self.assertNotEqual(
            dirs["{{ hyrule_prober_venv_dir }}"].get("owner"),
            "root",
            "the venv is built and installed into as the service user, so root "
            "ownership reintroduces the permission failure",
        )

    def test_service_user_tasks_get_a_writable_home(self):
        # The user is created with create_home: false, so $HOME resolves to a
        # /home/hyrule-prober that does not exist. git and pip both want a
        # writable HOME, and Ansible cannot place its tmpdir there either:
        #   [WARNING]: Unable to use '/home/hyrule-prober/.ansible/tmp' ...
        self.assertIs(self._task("Ensure hyrule-prober user exists")["ansible.builtin.user"]["create_home"], False)

        for task in self.tasks:
            if task.get("become_user") != SERVICE_USER:
                continue
            environment = task.get("environment") or {}
            self.assertIn(
                "HOME",
                environment,
                f"task {task['name']!r} runs as the home-less service user without a HOME override",
            )

    def test_pip_module_dependency_is_installed_on_the_target(self):
        # ansible.builtin.pip imports `packaging` under the TARGET's interpreter
        # (/usr/bin/python3), not inside the venv it manages. Debian 13 ships a
        # minimal python3 without it, so the editable install failed with
        # "Failed to import the required Python library (packaging)". Any role
        # reaching for the pip module has to install it explicitly.
        uses_pip_module = any("ansible.builtin.pip" in task for task in self.tasks)
        if not uses_pip_module:
            self.skipTest("role no longer uses ansible.builtin.pip")

        self.assertIn(
            "python3-packaging",
            self.defaults["hyrule_prober_packages"],
            "ansible.builtin.pip needs `packaging` on the target interpreter",
        )

    def test_checkout_never_waits_on_a_credential_prompt(self):
        # hyrule-prober was the only private app repo; the first apply hung its
        # clone on a username prompt. Public now, but a deploy must fail fast
        # rather than block if that ever changes again.
        checkout = self._task("Checkout hyrule-prober at the requested ref")
        self.assertEqual(checkout["environment"].get("GIT_TERMINAL_PROMPT"), "0")


if __name__ == "__main__":
    unittest.main()
