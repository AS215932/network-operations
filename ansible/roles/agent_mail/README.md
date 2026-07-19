# Agent Mail rollout runbook

This role stages a dedicated Stalwart host for Hyrule's API-only Agent Mail
product. It does not replace or share state with the corporate OpenBSD `mail`
host. The committed inventory is deliberately inert: apply, process start,
bootstrap, backups, public SMTP, and every launch approval are `false`.

## Fixed boundaries

- Hostname: `mx1.agentmail.hyrule.host`; service domain:
  `agentmail.hyrule.host`.
- Planned VM: Debian 13, 2 vCPU, 4 GB RAM, 80 GB disk, overlay IPv6
  `2a0c:b641:b50:2::110`, plus a new dedicated IPv4.
- Public ingress may contain only SMTP reception on TCP/25. Never publish
  SMTP submission (465/587), IMAP (143/993), POP (110/995), ManageSieve
  (4190), JMAP/admin, or webmail.
- HTTPS/JMAP on TCP/443 is limited to `api`, `mon`, and operator networks.
- Stalwart is pinned by tag and manifest digest. Before any apply, compare the
  pinned patch against current upstream security releases and review an
  upgrade as a separate immutable-digest change.

## Stage 1 — provision without a listener

1. Provision the dedicated VM and its 80 GB disk. Do not reuse
   `mail_failover_ipv4`; allocate a new IPv4 and configure its forward and
   reverse routing.
   The committed `agentmail` host is also a member of the `staged` inventory
   group, which the canonical drift/apply sweep excludes. After SSH and base
   reachability are proven, remove that membership in a reviewed change before
   treating the host as part of the managed fleet.
2. Put these values in Vault and expose them only to the approved Ansible apply
   job as environment variables:
   `AGENT_MAIL_DNS_TSIG_SECRET`, `AGENT_MAIL_WEBHOOK_SECRET`, and (temporarily)
   `AGENT_MAIL_RECOVERY_ADMIN_SECRET`. The DNS value must equal Knot's
   `hyrule-dns` key; the webhook value must also be stored as
   `mail_internal_webhook_secret` for Hyrule Cloud.
3. Keep `agent_mail_public_enabled: false`. Set `agent_mail_apply` and
   `agent_mail_start` only for the controlled host apply. A default invocation
   renders review artifacts and cannot touch the host.
4. Apply Knot and the firewall additions first. Confirm the Knot update ACL is
   restricted to `agentmail`'s exact IPv6 source.

## Stage 2 — one-time bootstrap

1. In one reviewed inventory change, set all three bootstrap controls to true:
   `agent_mail_bootstrap_enabled`,
   `agent_mail_bootstrap_firewall_enabled`, and the TCP/8080 rule's `enabled`
   field. Keep public SMTP false. Supply a new temporary recovery password of
   at least 32 characters.
2. Re-render and apply the firewall after enabling the TCP/8080 rule, then
   apply this role. Connect only from the ops prefix or VPN:

   ```sh
   ansible-playbook playbooks/firewall.yml --tags apply --limit agentmail \
     -e firewall_apply=true
   ansible-playbook playbooks/agent_mail.yml --tags apply --limit agentmail \
     -e agent_mail_apply=true
   ```

   Submit the rendered `/etc/agent-mail/bootstrap.json` to the bootstrap API:

   ```sh
   curl --fail-with-body --user "admin:$AGENT_MAIL_RECOVERY_ADMIN_SECRET" \
     -H 'Content-Type: application/json' \
     --data-binary @/etc/agent-mail/bootstrap.json \
     'http://[2a0c:b641:b50:2::110]:8080/api'
   ```

3. Capture the permanent administrator credential through the approved secret
   channel. Create a least-privilege API key with the Domain, Account, and
   required management permissions used by `hyrule-cloud`; store its bearer
   token in Vault as `mail_backend_token`.
4. Immediately set both bootstrap controls and the firewall rule back to
   false, remove `AGENT_MAIL_RECOVERY_ADMIN_SECRET`, re-apply this role and the
   firewall, and verify that TCP/8080 is neither published by Compose nor
   accepted by nftables.

The bootstrap plan uses RocksDB, the internal directory, a container-console
tracer, automatic DKIM, Let's Encrypt, and TSIG RFC2136 against Knot. Review
the generated response and resulting listeners before continuing.

## Stage 3 — converge post-bootstrap state

Install the matching `stalwart-cli` on an operator workstation. Use the
permanent administrator credential, always dry-run first, then apply the
staged idempotent plan:

```sh
export STALWART_URL='https://mx1.agentmail.hyrule.host'
export STALWART_USER='admin'
export STALWART_PASSWORD='<from-vault>'
stalwart-cli apply --file ansible/generated/agentmail/desired-state.ndjson --dry-run
stalwart-cli apply --file ansible/generated/agentmail/desired-state.ndjson --json
```

The plan enables firewall-protected Prometheus metrics and a signed webhook to
`https://cloud.hyrule.host/v1/internal/mail/events`. Verify an actual webhook
uses `X-Signature` with a base64 HMAC and that Hyrule Cloud returns HTTP 202.
Do not apply the plan if the CLI schema for the pinned Stalwart build rejects
it; update and review the artifact instead of bypassing validation.

## Stage 4 — readiness evidence

All items below must have durable evidence before changing a launch gate:

1. DNS: authoritative MX, SPF, DKIM, DMARC, MTA-STS, TLS reporting, and ACME
   records resolve consistently from both nameservers. Confirm DNS update
   scope cannot alter zones outside the managed Hyrule set.
2. PTR: the dedicated IPv4 and IPv6 reverse to
   `mx1.agentmail.hyrule.host`, whose forward records return the same addresses.
3. Backup: enable the local quiesced timer, transfer snapshots to an encrypted
   off-host repository, verify checksums, and complete a restore into an
   isolated VM. A same-disk tarball alone never satisfies
   `agent_mail_backup_restore_verified`.
4. Monitoring: apply the monitoring, Prometheus, and logs roles only after the
   host is reachable. Confirm node, readiness, Stalwart metrics, logs, disk,
   certificate, queue, delivery-failure, and webhook-failure signals.
5. Abuse/legal: approve terms, complaint intake, postmaster/abuse handling,
   malware suspension, rate limits, recipient limits, retention/deletion, and
   emergency shutdown ownership. Prove the Cloud worker's daily JMAP mailbox
   sweep permanently removes every message older than 30 days even when its
   local webhook index is empty; Stalwart's trash auto-expunge is only a
   secondary control.
6. Canaries: complete inbound, outbound, bounce, complaint, malware, expiry,
   webhook, Gmail, Outlook, and at least one independent-domain journey.
   Record exact prompts, redacted results, x402 spend, and elapsed time; do not
   publish placeholder success claims.

## Stage 5 — public SMTP launch

In one reviewed change, assign the dedicated IPv4 and set every readiness flag
to true. Then set `agent_mail_public_enabled`,
`agent_mail_smtp_firewall_enabled`, and both TCP/25 firewall rules' `enabled`
fields to true. The role refuses the change if any approval is missing, if
bootstrap/recovery mode remains, or if backups/start/apply are not enabled.

Before enabling Hyrule Cloud's public Agent Mail gate, verify its Vault-rendered
configuration has `MAIL_ENABLED=true`, both legal/abuse approvals true, the
backend token and both Fernet/HMAC secrets present, and domain-agent purchasing
enabled only if that product is also approved.

## Emergency shutdown and restore

1. Set Hyrule Cloud `MAIL_ENABLED=false` to stop new activation/send traffic.
2. Set `agent_mail_start=false` and apply the Agent Mail role. The role runs
   `docker compose down`, which stops queued outbound delivery while preserving
   the bind-mounted configuration and mailbox data.
3. Set `agent_mail_public_enabled`, its SMTP firewall twin, and both TCP/25
   rule fields false; apply the Agent Mail role and nftables. Verify the
   container is absent and the forward chain contains the outbound TCP/25 kill
   switch before treating public delivery as stopped.
4. Preserve logs and a quiesced snapshot before destructive investigation.
5. Restore only to an isolated host: verify the `.sha256`, stop Stalwart,
   extract with numeric ownership/xattrs/ACLs from `/`, then start without any
   public listener. Run integrity, DNS, webhook, and canary checks before
   re-enabling traffic.
