import json
import unittest
from copy import deepcopy
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO = Path(__file__).resolve().parents[2]
MOCK = yaml.safe_load((REPO / "tests/iac/mock_inventory.yml").read_text())


class MockRenderTest(unittest.TestCase):
    def render(self, template, context=None):
        context = MOCK if context is None else context
        env = Environment(
            loader=FileSystemLoader(str(template.parent)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        env.filters["bool"] = bool
        env.filters["to_json"] = json.dumps
        return env.get_template(template.name).render(**context)

    def test_vault_agent_config_renders_with_wrapped_secretid(self):
        rendered = self.render(REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2")
        self.assertIn('secret_id_response_wrapping_path = "auth/approle/role/hyrule-cloud/secret-id"', rendered)
        self.assertIn("remove_secret_id_file_after_reading = true", rendered)
        self.assertIn("mode = 0600", rendered)

    def test_vault_agent_config_renders_custom_token_sink_mode(self):
        context = deepcopy(MOCK)
        context["vault_agent_token_sink_mode"] = "0640"

        rendered = self.render(REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2", context)

        self.assertIn("mode = 0640", rendered)

    def test_vault_agent_config_escapes_reload_command_quotes(self):
        context = deepcopy(MOCK)
        context["vault_agent_templates"][0]["reload_command"] = (
            '/bin/grep -Eq "^NOC_APPROVAL_SIGNING_SECRET=.{32,}$" /opt/noc-agent/.env && '
            "/bin/systemctl restart noc-agent.service"
        )

        rendered = self.render(REPO / "ansible/roles/vault_agent/templates/vault-agent.hcl.j2", context)

        self.assertIn(r"\"^NOC_APPROVAL_SIGNING_SECRET=.{32,}$\"", rendered)
        self.assertNotIn('grep -Eq "^NOC_APPROVAL_SIGNING_SECRET=.{32,}$"', rendered)

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
        handlers = (REPO / "ansible/roles/monitoring/handlers/main.yml").read_text()

        self.assertIn('node_exporter_user="nobody"', task_file)
        self.assertIn('node_exporter_group="nobody"', task_file)
        self.assertIn('node_exporter_listen_address="{{ monitoring_node_listen }}"', task_file)
        self.assertIn('node_exporter_args=""', task_file)
        self.assertIn('node_exporter_flags="-S -s info -l daemon"', task_file)
        self.assertNotIn('node_exporter_flags="--web.listen-address=', task_file)
        self.assertIn('pkill -x {{ monitoring_node_service }}', handlers)
        self.assertIn('service {{ monitoring_node_service }} onestart', handlers)


if __name__ == "__main__":
    unittest.main()
