import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


class NotificationRoutingTest(unittest.TestCase):
    def test_alertmanager_has_one_case_service_delivery_path(self):
        config = (REPO / "configs/mon/alertmanager.yml.j2").read_text()
        self.assertIn("notification_route", config)
        self.assertIn("repeat_interval: 24h", config)
        self.assertIn("/webhook/alertmanager", config)
        self.assertNotIn("discord_configs:", config)

    def test_icinga_normal_notifications_are_single_shot_with_narrow_fallback(self):
        config = (REPO / "ansible/roles/icinga2/templates/notifications.conf.j2").read_text()
        self.assertIn("interval = 0", config)
        self.assertIn("icinga2_fallback_notification_interval", config)
        self.assertIn('assign where host.name in [ "noc", "mon" ]', config)
        self.assertIn('"alertmanager-health"', config)
        self.assertNotIn("FlappingStart", config)

    def test_legacy_gemini_alerts_are_removed_and_ai_rules_are_routed(self):
        rules = (REPO / "configs/mon/prometheus-rules/noc-tripwire.yml").read_text()
        self.assertNotIn("NOCAgentGeminiQuota", rules)
        self.assertIn("NOCAgentModelFallbackActive", rules)
        self.assertIn("notification_route: ai", rules)
        self.assertIn("notification_route: network", rules)

    def test_model_check_distinguishes_warning_from_unavailable(self):
        script = (REPO / "configs/mon/icinga2/scripts/check_noc_agent_model_health.sh").read_text()
        self.assertIn("WARNING - $detail", script)
        self.assertIn("readiness=$readiness", script)
        self.assertIn('[ "$runtime" = "ok" ]', script)
        self.assertIn("503) echo \"CRITICAL", script)

    def test_icinga_route_value_is_trimmed_and_allowlisted(self):
        script = (REPO / "configs/mon/icinga2/scripts/notify-noc-agent.sh").read_text()
        self.assertIn(").strip()", script)
        self.assertIn('{"network", "ai", "ci"}', script)

    def test_extmon_keeps_independent_critical_fallback_only(self):
        config = (REPO / "ansible/roles/extmon/templates/alertmanager.yml.j2").read_text()
        tasks = (REPO / "ansible/roles/extmon/tasks/main.yml").read_text()
        self.assertIn("receiver: noc-agent", config)
        self.assertIn("receiver: critical-discord", config)
        self.assertIn("discord_configs:", config)
        self.assertIn("webhook_configs:", config)
        self.assertIn("require both external alert delivery paths", tasks)
        self.assertIn("extmon_noc_alertmanager_webhook_url", tasks)


if __name__ == "__main__":
    unittest.main()
