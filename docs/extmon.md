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
- `prometheus-alertmanager` — Discord receiver, dedupe, repeat 1 h.
- `prometheus-node-exporter` — self-scrape + textfile collector for the
  OVH expiry script.
- All four packages from Debian 13 apt; no out-of-band binaries.

Loopback-only listeners; SSH from ops-prefix or AS215932 only; Alertmanager
reaches Discord directly via outbound TLS — no inbound exposure required.

## Probes

Configured in [`roles/extmon/defaults/main.yml`](../ansible/roles/extmon/defaults/main.yml):

- `extmon_http_targets` — public HTTPS endpoints (servify.network, api,
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
- [ ] Different geographic region from OVH FR (eg. NL, DE, UK, US-East).

### One-time VPS bring-up

1. Provision a small Debian-13 VPS (Vultr "Cloud Compute" or DO "Basic Droplet",
   1 GB RAM is enough). Add the ops public key during creation.
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
   ```
5. Render configs locally and review the diff:
   ```bash
   cd ansible
   ansible-playbook playbooks/extmon.yml --tags validate \
       --connection=local --skip-tags=snapshot --limit extmon
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
    prometheus-blackbox-exporter prometheus-node-exporter ovh-expiry-collector.timer
curl -s http://127.0.0.1:9090/api/v1/targets | jq '.data.activeTargets[] | {job, instance, health}'
curl -s 'http://127.0.0.1:9090/api/v1/query?query=probe_success' | jq
curl -s http://127.0.0.1:9100/metrics | grep ovh_service_expires_seconds
```

End-to-end smoke test from your workstation:

```bash
# Should produce a real Discord message:
ssh root@<extmon> 'amtool --alertmanager.url=http://127.0.0.1:9093 alert add \
    alertname=ExtmonSmokeTest severity=info'
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
- Webhook rotation: change `EXTMON_DISCORD_WEBHOOK_URL` and re-apply; the
  `alertmanager.yml` template + handler reload covers it.
- Billing: keep extmon's bill paid. There is no second-order monitor for
  extmon itself in this design — if you want one, the cheapest option is
  a free synthetic check from BetterStack/UptimeRobot pinging extmon's
  public v4 every minute.

## Out of scope (for now)

- Looking-glass / BGP-announce checks (RIPEstat API). Worth adding once
  the rest of extmon is stable; until then, downstream symptoms (probe
  failures) catch BGP withdrawals indirectly.
- Federation between extmon's Prometheus and mon's Prometheus. Useful for
  unified Grafana dashboards but not needed for alerting.
- Push-Pushover / push-SMS escalation when Discord itself is down.
