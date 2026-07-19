import ipaddress
import json
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "ansible/inventory"
ROLE = REPO / "ansible/roles/agent_mail"
EXPECTED_IMAGE = (
    "stalwartlabs/stalwart:v0.16.4@sha256:"
    "c8aee803933a643558a9afaa3c208d4175a4ac09884f555b821aa5df1e89230c"
)
FORBIDDEN_PUBLIC_PORTS = {110, 143, 465, 587, 993, 995, 4190}


def _yaml(path: Path):
    return yaml.safe_load(path.read_text())


class AgentMailContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hosts = _yaml(INVENTORY / "hosts.yml")["all"]["children"]
        cls.all_vars = _yaml(INVENTORY / "group_vars/all.yml")
        cls.host_vars = _yaml(INVENTORY / "host_vars/agentmail.yml")
        cls.defaults = _yaml(ROLE / "defaults/main.yml")
        cls.context = {
            **cls.all_vars,
            **cls.defaults,
            **cls.host_vars,
            "agent_mail_overlay_ipv6": cls.all_vars["peers"]["agentmail"]["ipv6"],
            "agent_mail_public_ipv4": "",
        }

    def render(self, name: str, context=None) -> str:
        env = Environment(
            loader=FileSystemLoader(str(ROLE / "templates")),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        env.filters["bool"] = bool
        env.filters["to_json"] = json.dumps
        return env.get_template(name).render(**(context or self.context))

    def test_dedicated_host_address_and_membership_are_explicit(self):
        linux = self.hosts["linux"]["hosts"]
        self.assertEqual(linux["agentmail"]["ansible_host"], "2a0c:b641:b50:2::110")
        self.assertIn("agentmail", self.hosts["infra_vms"]["hosts"])
        self.assertIn("agentmail", self.hosts["public_facing"]["hosts"])
        self.assertNotIn("agentmail", self.hosts["openbsd"]["hosts"])
        self.assertEqual(
            self.all_vars["peers"]["agentmail"]["ipv6"],
            linux["agentmail"]["ansible_host"],
        )
        self.assertIn(
            ipaddress.ip_address(linux["agentmail"]["ansible_host"]),
            ipaddress.ip_network(self.all_vars["infra_subnet"]),
        )

    def test_every_mutating_or_public_gate_defaults_false(self):
        gates = (
            "agent_mail_apply",
            "agent_mail_start",
            "agent_mail_public_enabled",
            "agent_mail_smtp_firewall_enabled",
            "agent_mail_bootstrap_enabled",
            "agent_mail_bootstrap_firewall_enabled",
            "agent_mail_backup_enabled",
            "agent_mail_legal_approved",
            "agent_mail_abuse_process_approved",
            "agent_mail_dns_verified",
            "agent_mail_ptr_verified",
            "agent_mail_backup_restore_verified",
            "agent_mail_canaries_verified",
        )
        for gate in gates:
            with self.subTest(gate=gate):
                self.assertIs(self.host_vars[gate], False)

    def test_stalwart_image_is_immutable_and_public_protocols_are_absent(self):
        self.assertEqual(self.defaults["agent_mail_image"], EXPECTED_IMAGE)
        compose = self.render("docker-compose.yml.j2")
        self.assertIn(EXPECTED_IMAGE, compose)
        self.assertIn("[2a0c:b641:b50:2::110]:443:443/tcp", compose)
        self.assertNotIn(":25:25/tcp", compose)
        self.assertNotIn(":8080:8080/tcp", compose)
        for port in FORBIDDEN_PUBLIC_PORTS:
            self.assertNotIn(f":{port}:{port}/tcp", compose)

        launch = dict(self.context)
        launch.update(
            agent_mail_public_enabled=True,
            agent_mail_public_ipv4="203.0.113.25",
        )
        launched = self.render("docker-compose.yml.j2", launch)
        self.assertIn("[2a0c:b641:b50:2::110]:25:25/tcp", launched)
        self.assertIn("203.0.113.25:25:25/tcp", launched)
        for port in FORBIDDEN_PUBLIC_PORTS:
            self.assertNotIn(f":{port}:{port}/tcp", launched)

    def test_bootstrap_listener_is_temporary_and_never_implies_smtp(self):
        bootstrap = dict(self.context)
        bootstrap["agent_mail_bootstrap_enabled"] = True
        rendered = self.render("docker-compose.yml.j2", bootstrap)
        self.assertIn("[2a0c:b641:b50:2::110]:8080:8080/tcp", rendered)
        self.assertNotIn(":25:25/tcp", rendered)

    def test_firewall_stages_only_tcp25_and_8080_as_disabled(self):
        rules = self.host_vars["firewall_extra_rules"]
        public = [rule for rule in rules if rule["dport"] == 25]
        bootstrap = [rule for rule in rules if rule["dport"] == 8080]
        self.assertEqual(len(public), 2)
        self.assertTrue(all(rule["enabled"] is False for rule in public))
        self.assertEqual(len(bootstrap), 1)
        self.assertIs(bootstrap[0]["enabled"], False)
        self.assertFalse(FORBIDDEN_PUBLIC_PORTS & {rule["dport"] for rule in rules})

    def test_bootstrap_and_declarative_plans_are_valid_secretless_json(self):
        bootstrap = json.loads(self.render("bootstrap.json.j2"))
        method, arguments, call_id = bootstrap["methodCalls"][0]
        self.assertEqual((method, call_id), ("x:Bootstrap/set", "bootstrap"))
        desired = arguments["update"]["singleton"]
        self.assertEqual(desired["serverHostname"], "mx1.agentmail.hyrule.host")
        self.assertEqual(desired["defaultDomain"], "agentmail.hyrule.host")
        self.assertEqual(desired["tracer"]["@type"], "Stdout")
        self.assertEqual(desired["dnsServer"]["@type"], "Tsig")
        self.assertEqual(desired["dnsServer"]["protocol"], "tcp")
        self.assertEqual(
            desired["dnsServer"]["key"],
            {
                "@type": "EnvironmentVariable",
                "variableName": "STALWART_DNS_TSIG_SECRET",
            },
        )

        operations = [
            json.loads(line)
            for line in self.render("desired-state.ndjson.j2").splitlines()
            if line.strip()
        ]
        self.assertEqual([op["object"] for op in operations], ["DataRetention", "Metrics", "WebHook"])
        retention = operations[0]["value"]
        self.assertIsNone(retention["archiveDeletedItemsFor"])
        self.assertIsNone(retention["archiveDeletedAccountsFor"])
        webhook = operations[2]["value"]["agent-mail-events"]
        self.assertEqual(webhook["eventsPolicy"], "include")
        self.assertEqual(webhook["url"], "https://cloud.hyrule.host/v1/internal/mail/events")
        self.assertEqual(webhook["signatureKey"]["variableName"], "STALWART_WEBHOOK_SECRET")
        combined = self.render("bootstrap.json.j2") + self.render("desired-state.ndjson.j2")
        self.assertNotIn("AGENT_MAIL_WEBHOOK_SECRET=", combined)
        self.assertNotIn("AGENT_MAIL_DNS_TSIG_SECRET=", combined)

    def test_dns_acl_and_cloud_network_flow_are_source_scoped(self):
        knot = (REPO / "ansible/roles/knot/templates/knot.conf.j2").read_text()
        dns_vars = _yaml(INVENTORY / "host_vars/dns.yml")
        api_vars = _yaml(INVENTORY / "host_vars/api.yml")
        self.assertIn("agentmail-update", knot)
        self.assertIn("peers.agentmail.ipv6", knot)
        self.assertIn("update-owner: name", knot)
        self.assertIn("update-owner-match: sub-or-equal", knot)
        self.assertIn("update-owner-name: [ agentmail.hyrule.host. ]", knot)
        self.assertIn("z.name == 'hyrule.host'", knot)
        update_rule = next(
            rule
            for rule in dns_vars["firewall_extra_rules"]
            if "dyn updates" in rule["comment"]
        )
        self.assertIn("{{ peers.agentmail.ipv6 }}", update_rule["src"])
        self.assertIn(
            {"to": "agentmail", "proto": "tcp", "port": 443, "purpose": "Agent Mail management JMAP and mailbox data plane"},
            api_vars["network_flows_outbound"],
        )

    def test_cloud_vault_template_is_fail_closed_and_uses_mvp_prices(self):
        template = (ROLE.parent / "vault_agent/templates/hyrule-cloud.env.ctmpl.j2").read_text()
        for setting in (
            'DOMAIN_AGENT_PURCHASES_ENABLED=',
            'DOMAIN_AGENT_ORDER_FERNET_KEY=',
            'MAIL_ENABLED={{ or .Data.data.mail_enabled "false" }}',
            'MAIL_LEGAL_APPROVED={{ or .Data.data.mail_legal_approved "false" }}',
            'MAIL_ABUSE_APPROVED={{ or .Data.data.mail_abuse_approved "false" }}',
            'MAIL_BACKEND_TOKEN={{ or .Data.data.mail_backend_token "" }}',
            'MAIL_CREDENTIAL_FERNET_KEY={{ or .Data.data.mail_credential_fernet_key "" }}',
            'MAIL_INTERNAL_WEBHOOK_SECRET={{ or .Data.data.mail_internal_webhook_secret "" }}',
            'PAYMENT_PRICE_MAIL_ACTIVATION=1.00',
            'PAYMENT_PRICE_MAIL_SEND=0.01',
        ):
            self.assertIn(setting, template)
        self.assertIn(
            '{{ $domainAgentLaunchAllowed := gt (len (parseJSON $domainTlds)) 0 }}',
            template,
        )
        self.assertIn(
            '$domainAgentPurchases) "true") $domainAgentLaunchAllowed',
            template,
        )
        self.assertNotIn("PAYMENT_PRICE_MAIL_AGENT_BASIC_DAY", template)
        self.assertNotIn("PAYMENT_PRICE_MAIL_STORAGE_GB_DAY", template)
        self.assertNotIn("PAYMENT_PRICE_MAIL_OUTBOUND_MESSAGE", template)

    def test_backup_and_launch_runbook_require_off_host_restore_evidence(self):
        backup = self.render("agent-mail-backup.sh.j2")
        self.assertLess(backup.index(" stop --timeout 120 stalwart"), backup.index("tar --acls"))
        self.assertIn("sha256sum", backup)
        readme = (ROLE / "README.md").read_text()
        self.assertIn("off-host", readme)
        self.assertIn("agent_mail_backup_restore_verified", readme)
        self.assertIn("Never publish", readme)

    def test_monitoring_is_private_and_not_public_status_claim(self):
        prometheus = _yaml(REPO / "configs/mon/prometheus.yml")
        job = next(
            item
            for item in prometheus["scrape_configs"]
            if item["job_name"] == "agent-mail-stalwart"
        )
        self.assertEqual(job["scheme"], "https")
        self.assertEqual(job["metrics_path"], "/metrics/prometheus")
        self.assertFalse(job["tls_config"]["insecure_skip_verify"])
        rules = _yaml(REPO / "configs/mon/prometheus-rules/agent-mail.yml")
        self.assertEqual(rules["groups"][0]["name"], "agent-mail")
        self.assertNotIn("public_status", (REPO / "configs/mon/prometheus-rules/agent-mail.yml").read_text())


if __name__ == "__main__":
    unittest.main()
