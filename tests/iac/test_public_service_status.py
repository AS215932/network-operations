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
        self.assertIn("job_name: blackbox-dns-hyrule", prometheus)
        job = prometheus.split("job_name: blackbox-dns-hyrule", 1)[1].split(
            "job_name:", 1
        )[0]
        self.assertIn("2a0c:b641:b50:2::10", job)
        self.assertIn("2001:41d0:304:300::7bfb", job)
        self.assertIn("module: [dns_hyrule]", job)

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
        self.assertIn('or vector(0)) == 0', rules)


if __name__ == "__main__":
    unittest.main()
