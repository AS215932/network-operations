"""Prevent root-run Go builds and unwritable service-user install paths."""
import unittest
from pathlib import Path

import yaml


class ProxyBuildOwnershipTest(unittest.TestCase):
    def test_build_and_install_have_distinct_privileges(self):
        for role in ("hyrule_network_proxy", "hyrule_tunnel_proxy"):
            with self.subTest(role=role):
                root = Path(__file__).resolve().parents[2]
                tasks = yaml.safe_load((root / "ansible/roles" / role / "tasks/apply.yml").read_text())
                build = next(t for t in tasks if t["name"].startswith("Build "))
                install = next(t for t in tasks if t["name"] == "Install built binary")
                directories = next(t for t in tasks if t["name"] == "Ensure build directories belong to the service user")
                self.assertTrue(build["become"])
                self.assertEqual(build["become_user"], "{{ " + role + "_user }}")
                args = build["ansible.builtin.command"]["argv"]
                output = args[args.index("-o") + 1]
                self.assertEqual(output, install["ansible.builtin.copy"]["src"])
                self.assertNotEqual(output, install["ansible.builtin.copy"]["dest"])
                self.assertTrue(install["ansible.builtin.copy"]["remote_src"])
                self.assertEqual(install["become_user"], "root")
                self.assertEqual(install["ansible.builtin.copy"]["owner"], "root")
                self.assertEqual(install["ansible.builtin.copy"]["mode"], "0755")
                self.assertFalse(directories["ansible.builtin.file"]["follow"])
                self.assertEqual(directories["ansible.builtin.file"]["owner"], build["become_user"])
                for path in [*build["environment"].values(), output.rsplit("/", 1)[0]]:
                    self.assertIn(path, directories["loop"])
                self.assertLess(tasks.index(directories), tasks.index(build))
                self.assertLess(tasks.index(build), tasks.index(install))
