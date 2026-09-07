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
