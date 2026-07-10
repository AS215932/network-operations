import json
import os
import subprocess
import tempfile
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
        self.assertIn("HYRULE_NETWORK_PROXY_TOKEN", rendered)
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
            "network_proxy_token",
            "customer_ipv6_supernet",
            "customer_ipv6_gateway",
            "customer_ipv6_dns",
        ):
            self.assertIn(f".Data.data.{key}", rendered)

    def test_reliability_governor_templates_render(self):
        context = deepcopy(MOCK)
        context.update(
            {
                "engineering_loop_user": "loop",
                "engineering_loop_group": "loop",
                "engineering_loop_install_dir": "/opt/engineering-loop",
                "engineering_loop_state_dir": "/var/lib/engineering-loop",
                "engineering_loop_env_file": "/opt/engineering-loop/.env",
                "engineering_loop_git_askpass_path": "/usr/local/lib/engineering-loop/git-askpass",
                "engineering_loop_github_app_token_path": "/usr/local/lib/engineering-loop/github-app-token",
                "engineering_loop_governor_wrapper_path": "/usr/local/lib/engineering-loop/run-reliability-governor",
                "engineering_loop_governor_repos": [
                    "AS215932/engineering-loop",
                    "AS215932/network-operations",
                ],
                "engineering_loop_governor_state_dir": "/var/lib/engineering-loop/reliability-governor",
                "engineering_loop_governor_limit": 20,
                "engineering_loop_governor_timer_calendar": "*:0/15",
                "engineering_loop_governor_timer_randomized_delay_sec": 120,
                "engineering_loop_knowledge_context_enabled": True,
                "engineering_loop_knowledge_mcp_url": "http://127.0.0.1:8767/mcp",
                "engineering_loop_knowledge_mcp_transport": "streamable-http",
                "engineering_loop_knowledge_context_budget": 6000,
                "engineering_loop_knowledge_context_timeout": 20,
            }
        )

        wrapper = self.render(
            REPO / "ansible/roles/engineering_loop/templates/run-reliability-governor.sh.j2",
            context,
        )
        service = self.render(
            REPO / "ansible/roles/engineering_loop/templates/hyrule-reliability-governor.service.j2",
            context,
        )
        timer = self.render(
            REPO / "ansible/roles/engineering_loop/templates/hyrule-reliability-governor.timer.j2",
            context,
        )

        subprocess.run(["bash", "-n"], input=wrapper, text=True, check=True)
        self.assertNotIn("{{", wrapper + service + timer)
        self.assertIn('if [ -r "/opt/engineering-loop/.env" ]; then', wrapper)
        self.assertIn('load_environment_file "/opt/engineering-loop/.env"', wrapper)
        self.assertNotIn('. "/opt/engineering-loop/.env"', wrapper)
        self.assertNotIn("set -a", wrapper)
        self.assertIn('args+=(--repo "AS215932/engineering-loop")', wrapper)
        self.assertIn("--dry-run", wrapper)
        self.assertIn("ExecStart=/usr/local/lib/engineering-loop/run-reliability-governor", service)
        self.assertIn("OnCalendar=*:0/15", timer)

    def test_reliability_governor_env_loader_does_not_evaluate_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            install_dir = tmp_path / "engineering-loop"
            loop_bin = install_dir / ".venv/bin/hyrule-engineering-loop"
            loop_bin.parent.mkdir(parents=True)
            capture_path = tmp_path / "capture"
            env_file = tmp_path / "engineering-loop.env"

            loop_bin.write_text(
                """#!/bin/bash
if [ "${1:-}" = "reliability-governor" ] && [ "${2:-}" = "--help" ]; then
  printf '%s\\n' "--knowledge-mcp-url"
  exit 0
fi
{
  printf 'token=%s\\n' "${ENGINEERING_LOOP_GITHUB_TOKEN:-}"
  printf 'gh=%s\\n' "${GH_TOKEN:-}"
  printf 'icinga=%s\\n' "${HYRULE_ICINGA_PASSWORD:-}"
  printf 'args=%s\\n' "$*"
} > "$TEST_CAPTURE_PATH"
"""
            )
            loop_bin.chmod(0o755)
            env_file.write_text(
                "\n".join(
                    [
                        "ENGINEERING_LOOP_GITHUB_AUTH_MODE=token",
                        "ENGINEERING_LOOP_GITHUB_TOKEN=abc$def;$(printf bad)`uname`",
                        "HYRULE_ICINGA_PASSWORD=p@ss$word;$(false)`date`",
                        "",
                    ]
                )
            )

            context = deepcopy(MOCK)
            context.update(
                {
                    "engineering_loop_install_dir": str(install_dir),
                    "engineering_loop_env_file": str(env_file),
                    "engineering_loop_git_askpass_path": str(tmp_path / "git-askpass"),
                    "engineering_loop_github_app_token_path": str(tmp_path / "github-app-token"),
                    "engineering_loop_governor_repos": ["AS215932/engineering-loop"],
                    "engineering_loop_governor_state_dir": str(tmp_path / "state"),
                    "engineering_loop_governor_limit": 20,
                    "engineering_loop_knowledge_context_enabled": True,
                    "engineering_loop_knowledge_mcp_url": "http://127.0.0.1:8767/mcp",
                    "engineering_loop_knowledge_mcp_transport": "streamable-http",
                    "engineering_loop_knowledge_context_budget": 6000,
                    "engineering_loop_knowledge_context_timeout": 20,
                }
            )

            wrapper = self.render(
                REPO / "ansible/roles/engineering_loop/templates/run-reliability-governor.sh.j2",
                context,
            )
            wrapper_path = tmp_path / "run-reliability-governor"
            wrapper_path.write_text(wrapper)
            wrapper_path.chmod(0o755)

            env = {**os.environ, "TEST_CAPTURE_PATH": str(capture_path)}
            subprocess.run([str(wrapper_path), "--dry-run"], env=env, check=True)

            capture = capture_path.read_text()
            self.assertIn("token=abc$def;$(printf bad)`uname`", capture)
            self.assertIn("gh=abc$def;$(printf bad)`uname`", capture)
            self.assertIn("icinga=p@ss$word;$(false)`date`", capture)
            self.assertIn("--dry-run", capture)

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
