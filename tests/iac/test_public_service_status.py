import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "configs" / "mon" / "prometheus-rules"


class PublicServiceStatusContracts(unittest.TestCase):
    def test_required_iac_job_runs_production_version_promtool(self):
        workflow = (REPO / ".github" / "workflows" / "iac-tests.yml").read_text()

        self.assertIn("name: Prometheus rule syntax", workflow)
        self.assertIn("--entrypoint /bin/promtool", workflow)
        self.assertIn("check rules configs/mon/prometheus-rules/*.yml", workflow)
        self.assertIn(
            "prom/prometheus@sha256:"
            "2d390eb0dcbb4518231dbd2d7b1aac7725a4bfb9205eb14c38ddebf88284f37f",
            workflow,
        )

    def test_hyrule_dns_probe_checks_both_authoritative_servers(self):
        blackbox = (REPO / "configs" / "mon" / "blackbox.yml").read_text()
        prometheus = (REPO / "configs" / "mon" / "prometheus.yml").read_text()

        self.assertIn("dns_hyrule:", blackbox)
        self.assertIn("query_name: hyrule.host", blackbox)
        self.assertIn("dns_hyrule_deploy:", blackbox)
        self.assertIn("query_name: deploy.hyrule.host", blackbox)
        self.assertIn("job_name: blackbox-dns-hyrule", prometheus)
        job = prometheus.split("job_name: blackbox-dns-hyrule", 1)[1].split(
            "job_name:", 1
        )[0]
        self.assertIn("2a0c:b641:b50:2::10", job)
        self.assertIn("2001:41d0:304:300::7bfb", job)
        self.assertIn("module: [dns_hyrule]", job)
        deploy_job = prometheus.split(
            "job_name: blackbox-dns-hyrule-deploy", 1
        )[1].split("job_name:", 1)[0]
        self.assertIn("2a0c:b641:b50:2::10", deploy_job)
        self.assertIn("2001:41d0:304:300::7bfb", deploy_job)
        self.assertIn("module: [dns_hyrule_deploy]", deploy_job)

    def test_probe_and_scrape_configs_activate_before_public_rules(self):
        install = (
            REPO / "ansible" / "roles" / "prometheus" / "tasks" / "install.yml"
        ).read_text()

        blackbox = install.index("Publish validated blackbox_exporter config")
        scrape = install.index("Publish validated Prometheus core config")
        rules = install.index("Publish validated rules to the live directory")
        flushes = [
            match.start()
            for match in re.finditer("ansible.builtin.meta: flush_handlers", install)
        ]
        self.assertEqual(len(flushes), 2)
        self.assertLess(blackbox, flushes[0])
        self.assertLess(flushes[0], scrape)
        self.assertLess(scrape, flushes[1])
        self.assertLess(flushes[1], rules)

    def test_every_public_alert_has_complete_safe_metadata(self):
        allowed_components = {
            "api_checkout",
            "compute",
            "intelligence",
            "domains_dns",
            "network_proxy",
        }
        public_blocks: list[str] = []
        for path in RULES.glob("*.yml"):
            text = path.read_text()
            blocks = re.split(r"(?=^\s{6}- alert: )", text, flags=re.MULTILINE)
            public_blocks.extend(block for block in blocks if 'public_status: "true"' in block)

        self.assertGreaterEqual(len(public_blocks), 9)
        for block in public_blocks:
            alert = re.search(r"- alert: ([A-Za-z0-9]+)", block)
            self.assertIsNotNone(alert)
            self.assertRegex(block, r"public_state: (degraded|outage)")
            component_match = re.search(r'public_components: "([a-z_,]+)"', block)
            self.assertIsNotNone(component_match, alert.group(1) if alert else block)
            components = set(component_match.group(1).split(","))
            self.assertTrue(components)
            self.assertLessEqual(components, allowed_components)
            self.assertRegex(block, r'public_title: "[^\n]+"')
            self.assertRegex(block, r'public_message: "[^\n]+"')

            public_lines = "\n".join(
                line for line in block.splitlines() if "public_" in line
            ).lower()
            for forbidden in ("servify.network", "2a0c:", "runbook", "xcp-ng", "loki"):
                self.assertNotIn(forbidden, public_lines)

    def test_status_rules_cover_customer_failure_domains(self):
        rules = (RULES / "hyrule-public-status.yml").read_text()
        for alert in (
            "HyrulePublicApiUnavailable",
            "HyrulePublicPaymentFailureRatio",
            "HyrulePublicComputeHostDegraded",
            "HyrulePublicRoutingDegraded",
            "HyrulePublicDNSDegraded",
            "HyrulePublicDNSOutage",
        ):
            self.assertIn(f"alert: {alert}", rules)
        self.assertIn('absent(probe_success{job="blackbox-http"', rules)
        self.assertIn('job="blackbox-dns-hyrule-deploy"', rules)
        self.assertIn("frr_bgp_peer_state != 1", rules)
        self.assertIn('max(up{job="hyrule-cloud"} offset 15m) == 1', rules)

    def test_bgp_alerts_use_frr_exporter_state_values(self):
        public_rules = (RULES / "hyrule-public-status.yml").read_text()
        tripwires = (RULES / "noc-tripwire.yml").read_text()
        icinga = (
            REPO / "configs" / "mon" / "icinga2" / "services" / "bgp.conf"
        ).read_text()

        self.assertIn("count(frr_bgp_peer_state != 1) > 0", public_rules)
        self.assertIn("expr: frr_bgp_peer_state != 1", tripwires)
        self.assertIn("count(frr_bgp_peer_state", icinga)
        self.assertIn("!= 1", icinga)
        for text in (public_rules, tripwires, icinga):
            self.assertNotIn("frr_bgp_peer_state != 6", text)

    def test_proxy_metrics_failure_is_only_publicly_degraded(self):
        rules = (RULES / "noc-tripwire.yml").read_text()
        block = rules.split("- alert: HyruleNetworkProxyDown", 1)[1].split(
            "- alert:", 1
        )[0]

        self.assertIn("public_state: degraded", block)
        self.assertIn("health checks are unavailable", block)
        self.assertNotIn("public_state: outage", block)


if __name__ == "__main__":
    unittest.main()
