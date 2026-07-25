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
        self.assertIn("agentmail", self.hosts["staged"]["hosts"])
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
        drift = (REPO / "scripts/ci/check-drift.sh").read_text()
        self.assertIn("all:!ci-pr:!staged", drift)

    def test_every_mutating_or_public_gate_defaults_false(self):
        gates = (
            "agent_mail_apply",
            "agent_mail_start",
            "agent_mail_public_enabled",
            "agent_mail_canary_enabled",
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
        for key in (
            "agent_mail_canary_inbound_ipv4_sources",
            "agent_mail_canary_inbound_ipv6_sources",
            "agent_mail_canary_outbound_ipv4_destinations",
            "agent_mail_canary_outbound_ipv6_destinations",
        ):
            with self.subTest(canary_networks=key):
                self.assertEqual(self.host_vars[key], [])

    def test_stalwart_image_is_immutable_and_public_protocols_are_absent(self):
        self.assertEqual(self.defaults["agent_mail_image"], EXPECTED_IMAGE)
        self.assertIn("docker-compose", self.defaults["agent_mail_packages"])
        self.assertNotIn("docker-compose-v2", self.defaults["agent_mail_packages"])
        compose = self.render("docker-compose.yml.j2")
        self.assertIn(EXPECTED_IMAGE, compose)
        self.assertIn("[2a0c:b641:b50:2::110]:443:443/tcp", compose)
        self.assertIn("com.docker.network.bridge.name: \"br-agentmail\"", compose)
        project = yaml.safe_load(compose)
        self.assertIs(project["networks"]["default"]["enable_ipv6"], True)
        self.assertEqual(
            project["networks"]["default"]["ipam"]["config"],
            [{"subnet": "fd21:5932:110::/64"}],
        )
        self.assertNotIn(":25:25/tcp", compose)
        self.assertNotIn(":8080:8080/tcp", compose)
        for port in FORBIDDEN_PUBLIC_PORTS:
            self.assertNotIn(f":{port}:{port}/tcp", compose)

        launch = dict(self.context)
        launch.update(
            agent_mail_public_enabled=True,
            agent_mail_public_ipv4="192.0.43.8",
        )
        launched = self.render("docker-compose.yml.j2", launch)
        self.assertIn("[2a0c:b641:b50:2::110]:25:25/tcp", launched)
        self.assertIn("192.0.43.8:25:25/tcp", launched)
        for port in FORBIDDEN_PUBLIC_PORTS:
            self.assertNotIn(f":{port}:{port}/tcp", launched)

        canary = dict(self.context)
        canary.update(
            agent_mail_canary_enabled=True,
            agent_mail_public_ipv4="192.0.43.8",
        )
        canary_compose = self.render("docker-compose.yml.j2", canary)
        self.assertIn("[2a0c:b641:b50:2::110]:25:25/tcp", canary_compose)
        self.assertIn("192.0.43.8:25:25/tcp", canary_compose)

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

        pre_established = self.host_vars["firewall_forward_pre_established_raw_nft"]
        forwarded = self.host_vars["firewall_forward_extra_raw_nft"]
        self.assertIn('ct status dnat oifname "{{ agent_mail_bridge_name }}" tcp dport 25', forwarded)
        self.assertIn('iifname "{{ agent_mail_bridge_name }}" tcp dport 25', forwarded)
        self.assertIn("Agent Mail outbound SMTP kill switch", pre_established)
        self.assertNotIn("Agent Mail outbound SMTP kill switch", forwarded)
        self.assertIn("Agent Mail private JMAP DNAT", forwarded)
        self.assertIn("agent_mail_canary_inbound_ipv4_sources", forwarded)
        self.assertIn("agent_mail_canary_inbound_ipv6_sources", forwarded)
        self.assertIn("agent_mail_canary_outbound_ipv4_destinations", forwarded)
        self.assertIn("agent_mail_canary_outbound_ipv6_destinations", forwarded)
        self.assertNotIn("0.0.0.0/0", forwarded)
        self.assertNotIn("::/0", forwarded)

        firewall = (ROLE.parent / "firewall/templates/nftables.conf.j2").read_text()
        self.assertLess(
            firewall.index("firewall_forward_pre_established_raw_nft"),
            firewall.index("ct state established,related accept"),
        )

    def test_shutdown_and_public_ipv4_guards_are_enforced(self):
        apply = (ROLE / "tasks/apply.yml").read_text()
        self.assertIn("down, --remove-orphans, --timeout", apply)
        self.assertIn("when: not (agent_mail_start | bool)", apply)
        self.assertIn("net.ipv6.conf.all.forwarding", apply)
        self.assertLess(
            apply.index("Stop the Agent Mail backup timer before runtime changes"),
            apply.index("Stop any in-flight Agent Mail backup before runtime changes"),
        )
        self.assertLess(
            apply.index("Stop any in-flight Agent Mail backup before runtime changes"),
            apply.index("Wait for any in-flight Agent Mail backup before runtime changes"),
        )
        self.assertLess(
            apply.index("Wait for any in-flight Agent Mail backup before runtime changes"),
            apply.index("Stop and remove Agent Mail when the start gate is disabled"),
        )
        self.assertGreaterEqual(
            apply.count("/run/lock/agent-mail-backup.lock"),
            4,
        )
        self.assertIn("flock, --wait", apply)

        validate = (ROLE / "tasks/validate.yml").read_text()
        self.assertIn("ipaddress.ip_address", validate)
        self.assertIn("address.is_global", validate)
        self.assertIn("not address.is_multicast", validate)
        self.assertIn("Inspect the dedicated SMTP IPv4 assignment", validate)
        self.assertIn('"{{ agent_mail_public_ipv4 }}/32"', validate)
        self.assertIn("agent_mail_public_ipv4 != (mail_failover_ipv4", validate)
        self.assertNotIn(
            "not (agent_mail_start | bool) or (agent_mail_apply | bool)",
            validate,
        )
        self.assertIn("Validate restricted Agent Mail canary networks", validate)
        self.assertIn("ipaddress.ip_network", validate)
        self.assertIn("network.prefixlen > 0", validate)
        self.assertIn("network.is_global", validate)
        self.assertIn(
            "_smtp_ipv4_sources == [agent_mail_canary_inbound_ipv4_sources]",
            validate,
        )
        self.assertIn(
            "_smtp_ipv6_sources == [agent_mail_canary_inbound_ipv6_sources]",
            validate,
        )
        self.assertIn(
            "Assert the temporary canary path is restricted and otherwise launch-ready",
            validate,
        )
        public_launch = validate.split(
            "- name: Assert every public-launch approval is explicit", maxsplit=1
        )[1]
        self.assertNotIn("- agent_mail_apply | bool", public_launch)
        self.assertIs(self.host_vars["firewall_preserve_external_tables"], True)

        firewall = (ROLE.parent / "firewall/templates/nftables.conf.j2").read_text()
        self.assertIn("destroy table inet filter", firewall)
        self.assertIn("firewall_preserve_external_tables", firewall)

        renderer = (REPO / "scripts/ci/render-all.sh").read_text()
        self.assertIn("extmon agent_mail", renderer)

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

    def test_runtime_secret_env_preserves_compose_special_characters(self):
        secret_context = dict(self.context)
        secret_context.update(
            agent_mail_bootstrap_enabled=True,
            agent_mail_dns_tsig_secret="tsig$HOME # key=value",
            agent_mail_webhook_secret="webhook${TOKEN} # key=value",
            agent_mail_recovery_admin_secret="recovery$TOKEN # key=value",
        )
        rendered = self.render("agent-mail.env.j2", secret_context)
        self.assertIn("STALWART_DNS_TSIG_SECRET='tsig$HOME # key=value'", rendered)
        self.assertIn("STALWART_WEBHOOK_SECRET='webhook${TOKEN} # key=value'", rendered)
        self.assertIn(
            "STALWART_RECOVERY_ADMIN='admin:recovery$TOKEN # key=value'",
            rendered,
        )
        validation = (ROLE / "tasks/validate.yml").read_text()
        self.assertIn('"\\\\" not in agent_mail_dns_tsig_secret', validation)
        self.assertIn("no_log: true", validation)

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
        self.assertLess(backup.index("mountpoint --quiet"), backup.index(" stop --timeout 120 stalwart"))
        self.assertIn('stat --file-system --format=%i "$backup_dir"', backup)
        self.assertIn('[[ "$backup_device" == "$root_device" ]]', backup)
        self.assertIn('[[ "$backup_device" == "$data_device" ]]', backup)
        self.assertIn('ps --all --quiet stalwart', backup)
        self.assertIn("docker inspect --format '{{.State.Status}}'", backup)
        self.assertIn("created|exited|dead", backup)
        self.assertIn("*) was_active=1", backup)
        self.assertIn('readonly min_capacity_bytes="107374182400"', backup)
        self.assertIn('readonly min_free_bytes="34359738368"', backup)
        self.assertLess(backup.index(" stop --timeout 120 stalwart"), backup.index("tar --acls"))
        self.assertIn('archive_name="${archive##*/}"', backup)
        self.assertIn('sha256sum "$archive_name"', backup)
        self.assertNotIn('sha256sum "$archive"', backup)
        self.assertIn("agent_mail_backup_last_success_timestamp_seconds", backup)
        self.assertIn('if [[ "$status" -eq 0 ]] && ! write_success_metric', backup)
        backup_service = self.render("agent-mail-backup.service.j2")
        self.assertIn("TimeoutStartSec=infinity", backup_service)
        self.assertEqual(self.defaults["agent_mail_backup_dir"], "/mnt/agent-mail-backup")
        self.assertEqual(self.defaults["agent_mail_backup_retention_days"], 2)
        readme = (ROLE / "README.md").read_text()
        self.assertIn("off-host", readme)
        self.assertIn("at least 100 GiB", readme)
        self.assertIn("agent_mail_backup_restore_verified", readme)
        self.assertIn("Never publish", readme)
        self.assertIn("firewall_apply=true", readme)
        self.assertIn("agent_mail_start=false", readme)
        self.assertIn("agent_mail_backup_enabled=false", readme)
        self.assertIn("outbound TCP/25 kill", readme)
        self.assertIn("Restricted pre-launch SMTP canary window", readme)
        self.assertIn("agent_mail_canary_inbound_", readme)
        self.assertIn("agent_mail_canary_outbound_", readme)

        apply = (ROLE / "tasks/apply.yml").read_text()
        self.assertIn("Inspect Agent Mail backup, root, and data filesystems", apply)
        self.assertIn(".stat.dev !=", apply)
        self.assertLess(
            apply.index("Wait for any in-flight Agent Mail backup before runtime changes"),
            apply.index("Install Agent Mail runtime and staged control artifacts"),
        )

    def test_protected_apply_logging_and_backup_monitoring_are_wired(self):
        workflow = _yaml(REPO / ".github/workflows/apply.yml")
        options = workflow[True]["workflow_dispatch"]["inputs"]["playbook"]["options"]
        self.assertIn("agent_mail", options)

        playbook = (REPO / "ansible/playbooks/agent_mail.yml").read_text()
        readme = (ROLE / "README.md").read_text()
        protected_command = (
            "gh workflow run apply.yml -F playbook=agent_mail "
            "-F limit=agentmail -F dry_run=false"
        )
        self.assertIn(protected_command, playbook)
        self.assertIn(protected_command, readme)

        log_vars = _yaml(INVENTORY / "host_vars/log.yml")
        agentmail_ingest = [
            rule
            for rule in log_vars["firewall_extra_rules"]
            if rule["dport"] == 6000 and "agentmail" in rule["comment"]
        ]
        self.assertEqual(
            agentmail_ingest,
            [
                {
                    "proto": "tcp",
                    "dport": 6000,
                    "src": "{{ peers.agentmail.ipv6 }}",
                    "comment": "Vector ingest from agentmail",
                }
            ],
        )

        disks = self.host_vars["monitoring_disks"]
        self.assertEqual(
            disks["disk /mnt/agent-mail-backup"],
            {"mountpoint": "/mnt/agent-mail-backup"},
        )
        backup_services = {
            service["name"]: service
            for service in self.host_vars["monitoring_extra_services"]
            if service["name"].startswith("agent-mail-backup-")
        }
        backup_timer = backup_services["agent-mail-backup-timer"]
        self.assertEqual(backup_timer["check_command"], "prom_systemd_unit")
        self.assertEqual(
            backup_timer["vars"]["systemd_unit"],
            "agent-mail-backup.timer",
        )
        self.assertEqual(
            backup_services["agent-mail-backup-service"]["check_command"],
            "prom_systemd_not_failed",
        )
        self.assertEqual(
            backup_services["agent-mail-backup-freshness"]["check_command"],
            "prom_agent_mail_backup_freshness",
        )
        self.assertEqual(
            self.host_vars["monitoring_node_textfile_dir"],
            "/var/lib/node_exporter/textfile_collector",
        )
        backup_checks = (
            REPO / "configs/mon/icinga2/services/agent-mail-backup.conf"
        ).read_text()
        self.assertIn("node_systemd_unit_state", backup_checks)
        self.assertIn("state=\\\"failed\\\"", backup_checks)
        self.assertIn(
            "agent_mail_backup_last_success_timestamp_seconds", backup_checks
        )
        node_exporter = (
            ROLE.parent / "monitoring/tasks/node_exporter.yml"
        ).read_text()
        self.assertIn("--collector.textfile.directory=", node_exporter)

    def test_protected_runner_maps_agent_mail_apply_values_from_vault(self):
        runner_env = (
            ROLE.parent / "vault_agent/templates/github-runner.env.ctmpl.j2"
        ).read_text()
        expected = {
            "AGENT_MAIL_DNS_TSIG_SECRET": "agent_mail_dns_tsig_secret",
            "AGENT_MAIL_WEBHOOK_SECRET": "agent_mail_webhook_secret",
            "AGENT_MAIL_RECOVERY_ADMIN_SECRET": "agent_mail_recovery_admin_secret",
            "AGENT_MAIL_PUBLIC_IPV4": "agent_mail_public_ipv4",
        }
        for variable, field in expected.items():
            with self.subTest(variable=variable):
                self.assertIn(variable, runner_env)
                self.assertIn(f".Data.data.{field}", runner_env)

        workflow = (REPO / ".github/workflows/apply.yml").read_text()
        self.assertIn("Validate Agent Mail runner secrets", workflow)
        self.assertIn(
            "for key in AGENT_MAIL_DNS_TSIG_SECRET AGENT_MAIL_WEBHOOK_SECRET",
            workflow,
        )
        runner_runbook = (
            REPO / "docs/runbooks/bootstrap-runner-vault.md"
        ).read_text()
        for field in expected.values():
            self.assertIn(field, runner_runbook)

    def test_staged_monitoring_is_inactive_and_not_public_status_claim(self):
        prometheus = _yaml(REPO / "configs/mon/prometheus.yml")
        jobs = {item["job_name"]: item for item in prometheus["scrape_configs"]}
        self.assertNotIn("agent-mail-stalwart", jobs)
        self.assertNotIn(
            "[2a0c:b641:b50:2::110]:9100",
            (REPO / "configs/mon/prometheus.yml").read_text(),
        )
        rules = _yaml(REPO / "configs/mon/prometheus-rules/agent-mail.yml")
        self.assertEqual(rules["groups"][0]["name"], "agent-mail")
        self.assertNotIn("public_status", (REPO / "configs/mon/prometheus-rules/agent-mail.yml").read_text())


if __name__ == "__main__":
    unittest.main()
