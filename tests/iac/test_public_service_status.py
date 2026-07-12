import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "configs" / "mon" / "prometheus-rules"


class PublicServiceStatusContracts(unittest.TestCase):
    def test_required_iac_job_stays_dependency_light(self):
        workflow = (REPO / ".github" / "workflows" / "iac-tests.yml").read_text()
        tier_zero = workflow.split("static-iac:", 1)[1].split(
            "ansible-idempotency:", 1
        )[0]

        self.assertIn("scripts/ci/iac-static.sh", tier_zero)
        self.assertNotIn("docker", tier_zero.lower())

        install = (
            REPO / "ansible" / "roles" / "prometheus" / "tasks" / "install.yml"
        ).read_text()
        self.assertIn("promtool", install)
        self.assertIn("--config.check", install)

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
        deploy_job = prometheus.split("job_name: blackbox-dns-hyrule-deploy", 1)[
            1
        ].split("job_name:", 1)[0]
        self.assertIn("2a0c:b641:b50:2::10", deploy_job)
        self.assertIn("2001:41d0:304:300::7bfb", deploy_job)
        self.assertIn("module: [dns_hyrule_deploy]", deploy_job)
        ipv4_job = prometheus.split("job_name: blackbox-dns-hyrule-ipv4", 1)[
            1
        ].split("job_name:", 1)[0]
        self.assertIn("46.105.40.223:53", ipv4_job)
        self.assertIn("54.38.14.218:53", ipv4_job)
        self.assertIn("module: [dns_soa_hyrule_host]", ipv4_job)
        deploy_ipv4_job = prometheus.split(
            "job_name: blackbox-dns-hyrule-deploy-ipv4", 1
        )[1].split("job_name:", 1)[0]
        self.assertIn("46.105.40.223:53", deploy_ipv4_job)
        self.assertIn("54.38.14.218:53", deploy_ipv4_job)
        self.assertIn("module: [dns_soa_deploy_hyrule_host]", deploy_ipv4_job)

        rules = (RULES / "hyrule-public-status.yml").read_text()
        # The delegated deploy zone is an independent public DNS signal: both the
        # degraded and outage rules must consume its probe job.
        self.assertIn('probe_success{job="blackbox-dns-hyrule-deploy"}', rules)
        # blackbox_exporter exposes no SOA-serial metric, so no rule may depend
        # on one (a probe_dns_serial reference would silently never fire).
        self.assertNotIn("probe_dns_serial", rules)

    def test_status_query_and_authoritative_probes_have_declared_flows(self):
        mon = (REPO / "ansible" / "inventory" / "host_vars" / "mon.yml").read_text()
        flows = (REPO / "ansible" / "inventory" / "network_flows.yml").read_text()

        self.assertIn('dport: 9090, src: "{{ peers.api.ipv6 }}"', mon)
        self.assertIn("Prometheus public-status queries from hyrule-cloud", mon)
        self.assertIn("from: api, to: mon, proto: tcp, port: 9090", flows)
        self.assertIn("from: mon, to: ns2, proto: udp, port: 53", flows)
        self.assertIn("from: mon, to: extmon, proto: tcp, port: 9115", flows)
        extmon = (
            REPO / "ansible" / "inventory" / "host_vars" / "extmon.yml"
        ).read_text()
        self.assertIn('dport: 9115, src: "{{ peers.mon.ipv6 }}"', extmon)

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
        self.assertIn("--syntax-only", install)

    def test_api_outage_requires_ipv4_and_ipv6_probe_failures(self):
        prometheus = (REPO / "configs" / "mon" / "prometheus.yml").read_text()
        extmon_blackbox = (
            REPO / "ansible" / "roles" / "extmon" / "templates" / "blackbox.yml.j2"
        ).read_text()
        rules = (RULES / "hyrule-public-status.yml").read_text()

        self.assertIn("job_name: blackbox-http-ipv4", prometheus)
        ipv4_job = prometheus.split("job_name: blackbox-http-ipv4", 1)[1].split(
            "job_name:", 1
        )[0]
        self.assertIn("module: [http_200_v4]", ipv4_job)
        self.assertIn("https://cloud.hyrule.host/health", ipv4_job)
        self.assertIn("2001:19f0:7402:0cd5:5400:06ff:fe40:7112", ipv4_job)
        self.assertIn("http_200_v4:", extmon_blackbox)
        strict_module = extmon_blackbox.split("http_200_v4:", 1)[1].split("\n\n  ", 1)[
            0
        ]
        self.assertIn("valid_status_codes: [200]", strict_module)
        for alert in (
            "HyrulePublicApiUnavailable",
            "HyrulePublicComputeControlPlaneUnavailable",
            "HyrulePublicApiAddressFamilyDegraded",
        ):
            block = rules.split(f"- alert: {alert}", 1)[1].split("- alert:", 1)[0]
            self.assertIn('job="blackbox-http-ipv4"', block)
            self.assertIn('job="blackbox-http"', block)
            self.assertNotIn("absent(", block)

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
            public_blocks.extend(
                block for block in blocks if 'public_status: "true"' in block
            )

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
            "HyrulePublicApiAddressFamilyDegraded",
            "HyrulePublicPaymentFailureRatio",
            "HyrulePublicComputeHostDegraded",
            "HyrulePublicRoutingDegraded",
            "HyrulePublicDNSDegraded",
            "HyrulePublicDNSOutage",
        ):
            self.assertIn(f"alert: {alert}", rules)
        self.assertNotIn('absent(probe_success{job="blackbox-http"', rules)
        self.assertIn('job="blackbox-dns-hyrule-deploy"', rules)
        self.assertIn("frr_bgp_peer_state != 1", rules)
        self.assertIn('absent(up{job="node-hypervisor"})', rules)
        self.assertIn('up{job="frr"} == 0', rules)
        self.assertNotIn("unless on(instance)", rules)
        self.assertIn('max(up{job="hyrule-cloud"} offset 15m) == 1', rules)
        self.assertIn(
            '(count(probe_success{job="blackbox-dns-hyrule"}) or vector(0)) != 2',
            rules,
        )
        self.assertIn(
            'count(probe_success{job="blackbox-dns-hyrule-deploy"}) == 2',
            rules,
        )
        self.assertIn('job="blackbox-dns-hyrule-ipv4"', rules)
        self.assertIn('job="blackbox-dns-hyrule-deploy-ipv4"', rules)

    def test_public_provisioning_ratio_requires_multiple_failures(self):
        rules = (RULES / "hyrule-payments.yml").read_text()
        block = rules.split("- alert: HyruleVMProvisionFailureRatio", 1)[1].split(
            "- alert:", 1
        )[0]

        self.assertIn(
            'sum(increase(hyrule_vm_provision_total{result="failed"}[1h])) >= 3',
            block,
        )
        self.assertIn("> 0.2", block)

    def test_bgp_alerts_use_frr_exporter_state_values(self):
        public_rules = (RULES / "hyrule-public-status.yml").read_text()
        tripwires = (RULES / "noc-tripwire.yml").read_text()
        icinga = (
            REPO / "configs" / "mon" / "icinga2" / "services" / "bgp.conf"
        ).read_text()

        self.assertIn("count(frr_bgp_peer_state != 1) > 0", public_rules)
        deploy = (REPO / "scripts" / "deploy-exporters.sh").read_text()
        self.assertGreaterEqual(deploy.count("--collector.bgp6"), 2)
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
