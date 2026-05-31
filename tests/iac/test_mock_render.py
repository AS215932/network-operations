import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO = Path(__file__).resolve().parents[2]
MOCK = yaml.safe_load((REPO / "tests/iac/mock_inventory.yml").read_text())


class MockRenderTest(unittest.TestCase):
    def render(self, template):
        env = Environment(
            loader=FileSystemLoader(str(template.parent)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        env.filters["bool"] = bool
        return env.get_template(template.name).render(**MOCK)

    def test_vault_agent_config_renders_with_wrapped_secretid(self):
        rendered = self.render(REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2")
        self.assertIn('secret_id_response_wrapping_path = "auth/approle/role/hyrule-cloud/secret-id"', rendered)
        self.assertIn("remove_secret_id_file_after_reading = true", rendered)

    def test_hyrule_cloud_vault_render_hook_renders_required_keys(self):
        rendered = self.render(REPO / "ansible/roles/hyrule_cloud/templates/vault-render-hook.sh.j2")
        for key in MOCK["hyrule_cloud_required_env_keys"]:
            self.assertIn(key, rendered)

    def test_github_runner_vault_template_renders_without_cloud_token(self):
        rendered = self.render(REPO / "ansible/roles/vault_agent/templates/github-runner.env.ctmpl.j2")
        self.assertIn("kv/data/ci-runner", rendered)
        self.assertNotIn("XO_TOKEN", rendered)

    def test_hyrule_cloud_vault_template_contains_secret_keys(self):
        rendered = self.render(REPO / "ansible/roles/vault_agent/templates/hyrule-cloud.env.ctmpl.j2")
        for key in (
            "xo_token",
            "sr_uuid",
            "vm_network_uuid",
            "openprovider_username",
            "openprovider_password",
            "payment_wallet",
            "tsig_secret",
            "db_password",
        ):
            self.assertIn(f".Data.data.{key}", rendered)

    def test_hyrule_mcp_hosts_renders_freebsd_metadata_and_aliases(self):
        rendered = self.render(REPO / "configs/hyrule-mcp-hosts.yml.j2")
        self.assertIn("cr1-nl1:", rendered)
        self.assertIn("os_family: freebsd", rendered)
        self.assertIn("init_system: service", rendered)
        self.assertIn('      - "cr1.nl1"', rendered)
        self.assertIn("aliases: []", rendered)

    def test_freebsd_node_exporter_task_configures_syslog_output(self):
        task_file = (REPO / "ansible/roles/monitoring/tasks/node_exporter.yml").read_text()
        self.assertIn('node_exporter_user="nobody"', task_file)
        self.assertIn('node_exporter_group="nobody"', task_file)
        self.assertIn('node_exporter_listen_address="{{ monitoring_node_listen }}"', task_file)
        self.assertIn('node_exporter_args=""', task_file)
        self.assertIn('node_exporter_flags="-S -s info -l daemon"', task_file)
        self.assertNotIn('node_exporter_flags="--web.listen-address=', task_file)


if __name__ == "__main__":
    unittest.main()
