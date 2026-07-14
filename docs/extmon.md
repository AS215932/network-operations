# extmon — external monitoring host

## Why this host exists

On 2026-05-07 the OVH failover IPv4 `46.105.40.223` expired due to non-payment.
NAT64 broke; every IPv4-egress packet from AS215932 timed out. The in-house
Icinga check `nat64-ipv4-reachability` *did* flip to `CRITICAL` — but the
`Problem` notification curl from `mon` to `discordapp.com` (IPv4-only,
DNS64-synthesised AAAA) had to traverse NAT64 to reach Discord. NAT64 was
broken. The notification silently failed (`curl: (7) Failed to connect …
after 15369 ms`). No alert reached operators until the IP came back online
~8.5 hours later, when the `Recovery` notification finally went through.

`extmon` is the architectural fix: a small VPS at a different provider, a
different ASN, a different country, and a different billing relationship.
It probes AS215932 from the open internet — the same vantage real users
have — and posts alerts to Discord directly via native v4/v6. An OVH-side
outage cannot suppress its alarm because nothing about its alert path
depends on AS215932 or OVH.

## Stack

- `prometheus-blackbox-exporter` — probes (HTTP, TCP, ICMP, DNS, TLS cert).
- `prometheus` — scrapes blackbox every 30 s, evaluates alert rules.
- `prometheus-alertmanager` — NOC case delivery, critical direct-Discord
  fallback, dedupe, transport reassertion every 24 h.
- `prometheus-node-exporter` — self-scrape + textfile collector for the
  OVH expiry script.
- `routinator` — local RPKI validator for AS215932 prefix validity.
- `bgpalerter` — RIPE RIS Live based hijack/withdrawal/RPKI/path alerting.
- `extmon-bgp-agent` — custom Prometheus exporter for RIPEstat, bgp.tools,
  Cloudflare Radar, Routinator, and BGPalerter webhooks.
- `extmon-diag-agent` — token-protected active diagnostics for Hyrule Cloud
  MX/network checks.

BGPalerter is the one pinned upstream binary; all other core packages come
from Debian/CAIDA apt repositories.

Loopback-only listeners; SSH from ops-prefix or AS215932 only. Alertmanager
delivers all alerts to NOC Agent, while critical alerts also reach Discord
directly over outbound TLS — no inbound exposure required.

`extmon-diag-agent` is loopback-only by default; the intended caller (the
Hyrule Cloud diagnostics API) reaches it over an SSH port-forward, matching the
other loopback services. To let a caller reach it directly, bind
`extmon_diag_agent_listen` to a reachable address and add a matching
`firewall_extra_rules` row in `host_vars/extmon.yml` plus the flow in
`docs/network-flows.md` — the host firewall is owned by `roles/firewall`
(nftables), not UFW. Do this only after the agent's input-validation hardening lands —
tracked in AS215932/network-operations#361 — since it performs active probes.

## Probes

Configured in [`roles/extmon/defaults/main.yml`](../ansible/roles/extmon/defaults/main.yml):

- `extmon_http_targets` — public HTTPS endpoints (hyrule.host, cloud,
  hyrule, mon).
- `extmon_icmp_v4_targets` — failover IPs that *must* be reachable from the
  open internet (`46.105.40.223`, `51.91.236.215`, `54.38.14.218`).
- `extmon_icmp_v6_targets` — public AS215932 v6 services.
- `extmon_tcp_v4_targets` — mail SMTP/IMAP ports.
- `extmon_dns_targets` × `extmon_dns_zones` — SOA queries against ns1 + ns2
  for every managed zone.

The OVH expiry collector polls `/dedicated/server/{name}/serviceInfos`,
`/ip/{ip}/serviceInfos` for each failover IP, and `/me/bill` for unpaid
invoices, then emits Prometheus textfile metrics. Alert thresholds: <14 d
warning, <7 d critical.

## Provisioning

### Pre-provisioning checklist

- [ ] Different cloud account from OVH (different card billing cycle).
- [ ] Different ASN — Vultr (AS20473) or DigitalOcean (AS14061) recommended.
- [ ] Different geographic region from OVH FR (Vultr London is the current target).
- [ ] 4 GB RAM + 2 GB swap. BGPalerter's upstream docs require about 4 GB.

### One-time VPS bring-up

1. Provision a Debian-13 VPS at Vultr London, 4 GB RAM, 2 GB swap. Add the ops public key during creation.
2. SSH in as `root` and verify reachability:
   ```bash
   ssh -i ~/.ssh/id_servify root@<extmon-public-v4>
   ```
3. Add the host's public v4 + v6 to:
   - `ansible/inventory/hosts.yml` → `external.hosts.extmon.ansible_host`
   - `ansible/inventory/group_vars/all.yml` → `peers.extmon.ipv4` / `.ipv6`
4. Generate a fresh Discord webhook (Discord channel → Edit → Integrations →
   Webhooks → New Webhook). **Use the `https://discord.com/...` URL form,
   not `discordapp.com`** — the latter is IPv4-only and the entire reason
   we have this host. Save it to your shell:
   ```bash
   export EXTMON_DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
   export EXTMON_BGP_CLOUDFLARE_API_TOKEN='...optional radar token...'
   export EXTMON_BGP_INGEST_TOKEN='...same as HYRULE_BGP_INGEST_TOKEN...'
   export EXTMON_DIAG_AGENT_TOKEN='...random bearer token...'
   export EXTMON_NOC_ALERTMANAGER_WEBHOOK_URL='http://[2a0c:b641:b50:2::a0]:8000/webhook/alertmanager'
   ```
5. Render configs locally and review the diff:
   ```bash
   cd ansible
   ansible-playbook playbooks/extmon.yml --tags validate \
       --connection=local --limit extmon
   git diff generated/extmon/
   ```
6. Apply:
   ```bash
   set -a; source ../secrets.local.sh; set +a
   ansible-playbook playbooks/extmon.yml --tags apply \
       -e '{"extmon_apply":true}' --limit extmon
   ```

### Verifying after first apply

From extmon (`ssh root@<extmon-public-v4>`):

```bash
systemctl is-active prometheus prometheus-alertmanager \
    prometheus-blackbox-exporter prometheus-node-exporter ovh-expiry-collector.timer \
    routinator bgpalerter extmon-bgp-agent extmon-diag-agent
curl -s http://127.0.0.1:9090/api/v1/targets | jq '.data.activeTargets[] | {job, instance, health}'
curl -s 'http://127.0.0.1:9090/api/v1/query?query=probe_success' | jq
curl -s http://127.0.0.1:9100/metrics | grep ovh_service_expires_seconds
curl -s http://127.0.0.1:9188/health
curl -s http://127.0.0.1:9188/metrics | grep bgp_source_up
curl -s http://127.0.0.1:8011/status
curl -s 'http://127.0.0.1:8323/validity?asn=AS215932&prefix=2a0c:b641:b50::/44'
```

End-to-end smoke tests from your workstation:

```bash
# Warning: one persistent case card in #noc, with no direct duplicate.
ssh root@<extmon> 'amtool --alertmanager.url=http://127.0.0.1:9093 alert add \
    alertname=ExtmonWarningSmokeTest severity=warning notification_route=network'

# Critical: one persistent #noc case card plus the independent fallback post.
ssh root@<extmon> 'amtool --alertmanager.url=http://127.0.0.1:9093 alert add \
    alertname=ExtmonCriticalSmokeTest severity=critical notification_route=network'

# Resolve both test alerts and verify the same case cards update in place.
ssh root@<extmon> 'amtool --alertmanager.url=http://127.0.0.1:9093 alert add \
    alertname=ExtmonWarningSmokeTest severity=warning notification_route=network \
    --end="$(date --iso-8601=seconds)"'
ssh root@<extmon> 'amtool --alertmanager.url=http://127.0.0.1:9093 alert add \
    alertname=ExtmonCriticalSmokeTest severity=critical notification_route=network \
    --end="$(date --iso-8601=seconds)"'
```

## Operating

- View dashboards / silences via SSH tunnel:
  ```bash
  ssh -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 root@<extmon>
  ```
  then open http://localhost:9090 / http://localhost:9093 in your browser.
- Add new probe targets by editing
  [`roles/extmon/defaults/main.yml`](../ansible/roles/extmon/defaults/main.yml)
  and re-running the apply playbook.
- Webhook rotation: change `EXTMON_DISCORD_WEBHOOK_URL` or
  `EXTMON_NOC_ALERTMANAGER_WEBHOOK_URL` and re-apply; the `alertmanager.yml`
  template + handler reload covers both.
- Billing: keep extmon's bill paid. There is no second-order monitor for
  extmon itself in this design — if you want one, the cheapest option is
  a free synthetic check from BetterStack/UptimeRobot pinging extmon's
  public v4 every minute.

## BGP monitoring scope

AS215932 monitoring is explicit, not inferred from service probes:

- ASN: `AS215932`
- Prefix: `2a0c:b641:b50::/44`
- Expected origin: `AS215932`
- RPKI max length: `/48`
- Public feeds: RIPEstat/RIS, bgp.tools export, Cloudflare Radar when token is set
- Local validator: Routinator on loopback
- Realtime alerting: BGPalerter reportHTTP into `extmon-bgp-agent`

Critical alerts retain direct Discord delivery from extmon as an independent
fallback. The NOC Agent webhook owns persistent case cards for both warning and
critical alerts; neither path is allowed to replace the other for criticals.

## Out of scope (for now)

- Push-Pushover / push-SMS escalation when Discord itself is down.
- Long-term BGPStream artifact retention beyond the Hyrule Cloud paid job API.
