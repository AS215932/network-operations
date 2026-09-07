import json
import unittest
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parents[2]


class LoopRetirementTest(unittest.TestCase):
    def test_retired_host_renders_no_active_monitoring_objects(self):
        template = (REPO / "ansible/roles/monitoring/templates/icinga_host.conf.j2").read_text()
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.filters["to_json"] = json.dumps
        rendered = environment.from_string(template).render(
            monitoring_retired=True, inventory_hostname="loop"
        )
        self.assertIn("Retired host loop", rendered)
        self.assertNotIn("object Host", rendered)
        self.assertNotIn("object Service", rendered)

    def test_noc_backends_do_not_send_work_to_retired_runtime(self):
        for path in ["configs/noc-agent.env.j2", "ansible/roles/vault_agent/templates/noc-agent.env.ctmpl.j2"]:
            text = (REPO / path).read_text()
            self.assertIn("NOC_PROACTIVE_HANDOFF_ENABLED=0", text)
            self.assertIn("NOC_INSIGHT_RECORDS_ENABLED=0", text)
        host = yaml.safe_load((REPO / "ansible/inventory/host_vars/noc.yml").read_text())
        for key in ["noc_engineering_handoff_delivery_enabled", "noc_disk_alert_handoff_enabled", "noc_agent_core_trace_enabled"]:
            self.assertFalse(host[key])

    def test_retirement_preserves_data_and_disables_producers_first(self):
        play = yaml.safe_load((REPO / "ansible/playbooks/retire-loop.yml").read_text())[0]
        units = play["vars"]["retired_loop_units"]
        self.assertLess(units.index("hyrule-engineering-loop.timer"), units.index("hyrule-engineering-loop.service"))
        self.assertLess(units.index("vault-agent-agent-core-collector.service"), units.index("agent-core-collector.service"))
        for task in play["tasks"]:
            self.assertNotIn("ansible.builtin.file", task)
            self.assertNotIn("ansible.builtin.shell", task)

    def test_runtime_controls_override_stale_vault_retirement_flags(self):
        host = yaml.safe_load((REPO / "ansible/inventory/host_vars/noc.yml").read_text())
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.filters["bool"] = bool
        text = environment.from_string(
            (REPO / "ansible/roles/noc_agent/templates/runtime.env.j2").read_text()
        ).render(**{**yaml.safe_load((REPO / "ansible/roles/noc_agent/defaults/main.yml").read_text()), **host})
        values = dict(line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#"))
        for key in [
            "NOC_ENGINEERING_HANDOFF_DELIVERY_ENABLED", "NOC_DISK_ALERT_HANDOFF_ENABLED",
            "HYRULE_NOC_AGENT_CORE_TRACE", "NOC_PROACTIVE_HANDOFF_ENABLED", "NOC_INSIGHT_RECORDS_ENABLED",
        ]:
            self.assertEqual(values[key], "0")
        self.assertEqual(values["NOC_CASESERVICE_REACTIVE_REPORT"], "0")
        self.assertEqual(values["NOC_CASE_ATTENTION_ENABLED"], "0")
        self.assertEqual(values["NOC_CASE_OUTBOX_ENABLED"], "1")
        self.assertEqual(values["HYRULE_NOC_AGENT_CORE_TRACE_COLLECTOR_URL"], '""')
        self.assertFalse(any("SECRET" in key or "TOKEN" in key or "PASSWORD" in key for key in values))
        for name in ["noc-agent.service", "noc-agent-bot.service"]:
            unit = (REPO / "configs" / name).read_text()
            self.assertLess(unit.index("EnvironmentFile=/opt/noc-agent/.env"),
                            unit.index("EnvironmentFile=/etc/noc-agent/runtime.env"))

    def test_runtime_controls_are_installed_before_units_for_both_secret_backends(self):
        tasks = yaml.safe_load((REPO / "ansible/roles/noc_agent/tasks/main.yml").read_text())
        runtime = next(task for task in tasks if task.get("template", {}).get("src") == "runtime.env.j2")
        self.assertNotIn("when", runtime)
        self.assertEqual(runtime["template"]["owner"], "root")
        self.assertEqual(runtime["template"]["mode"], "0644")
        self.assertIn("restart noc-agent", runtime["notify"])
        self.assertIn("restart noc-agent-bot", runtime["notify"])
        unit = next(task for task in tasks if task.get("copy", {}).get("dest") == "/etc/systemd/system/noc-agent.service")
        self.assertLess(tasks.index(runtime), tasks.index(unit))

    def test_noc_outbox_validation_matches_configured_worker_state(self):
        play = yaml.safe_load((REPO / "ansible/playbooks/noc.yml").read_text())[0]
        conditions = play["post_tasks"][0]["block"][0]["until"]
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.filters["bool"] = bool
        for enabled in (False, True):
            for running in (False, True):
                for reported_enabled in (False, True):
                    health = {"status": 200, "json": {"status": "ok", "backend": "PostgresCaseStore",
                        "outbox_worker": {"enabled": reported_enabled, "running": running}}}
                    matches = all(environment.compile_expression(condition)(
                        noc_case_outbox_enabled=enabled, noc_agent_case_health=health) for condition in conditions)
                    self.assertEqual(matches, enabled == running == reported_enabled)
        health["json"]["outbox_worker"] = {}
        self.assertFalse(all(environment.compile_expression(condition)(
            noc_case_outbox_enabled=False, noc_agent_case_health=health) for condition in conditions))
