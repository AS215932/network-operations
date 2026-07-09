# Log-Pipeline Post-Mortem — Issue #374

**Date:** 2026-07-09  
**Issue:** https://github.com/AS215932/network-operations/issues/374  
**Severity:** Critical — silent one-month data loss on `rtr`, FreeBSD routers (`cr1-*`) and `mail` absent from Loki  
**Authors:** Engineering Loop (auto-generated remediation specification)  

## Executive Summary

On 2026-07-09 a routine triage of the `rtr!disk disk /` Icinga warning revealed that the `rtr` Vector agent had shipped **zero** log lines to Loki since it started on 2026-06-05. The 300 MB disk buffer (`/var/lib/vector/buffer/v2/aggregator`) filled within ~10 h and then sat static for **one month** with `when_full = "block"`.

The process reported healthy (`/health` returned 200, systemd unit `active`) while the sink task was dead. A restart immediately surfaced:

```text
Error encountered during buffer read ... a data file contains a partially-written record.
error_code="partial_write" error_type="reader_failed"
```

Vector dropped exactly two corrupted events, resumed shipping, and the buffer drained. The disk warning cleared.

A wider audit showed that **cr1-nl1**, **cr1-de1**, **cr1-ch1**, and **mail** also produce no queryable `host` label in Loki. The FreeBSD routers have no Vector agent (by design — they use `syslogd @@log:6514` per issue #17), but nothing from that path is visible under the `host` label either.

Concrete consequence: when `cr1-ch1` died on 2026-07-08 22:51 UTC we had **no post-mortem logs** at all.

This document records the root causes, the exact remediation steps, and the alert-gaps that must be closed to prevent a repeat.

---

## 1. Root Cause — Wedged Vector Agent on `rtr` (Blocked Disk Buffer)

### What happened

- `rtr` runs a Vector agent that reads `journald` and ships to the aggregator on `log:6000`.
- The sink `[sinks.aggregator]` uses a **disk buffer**:
  - `max_size = 300000000` (~286 MiB)
  - `when_full = "block"`
- `rtr` has a **2.8 GB root filesystem**. When the buffer fills, it keeps growing (or rather, stops draining but retains full allocation) and hides the failure. The disk pressure was the only external signal.
- A **partial-write record** in the buffer (caused either by an unclean shutdown or a Vector 0.55.0 bug) deadlocked the reader task. Because the topology was blocked, no new events were consumed, but the process stayed up.

### Why it went undetected for a month

- There is **no per-host log-liveness alert** today. Prometheus rules on `mon` check:
  - `up{job="vector"}` — only the *aggregator* on `log`, not agents.
  - `VectorBufferStalling` — looks at the aggregator's own Loki-sink buffer, not remote agents.
- systemd showed the unit as `active` and Vector's local API returned HTTP 200.
- The only outward symptom was a slow disk-filling warning, which is a generic metric and shares the host with many other services.

### Required fix

1. Change `when_full` from `"block"` to `"drop_newest"` on space-constrained agents (rtr, FreeBSD syslog-forwarders that may cache locally, small-root VMs).
   - File: `ansible/roles/logs/templates/vector-agent.toml.j2`
   - Change the `[sinks.aggregator.buffer]` stanza and make `when_full` a role variable (e.g. `logs_agent_buffer_when_full`) so it can be overridden per-host.
2. Lower the buffer cap on `rtr` and similarly constrained hosts.
   - `rtr` currently has `logs_agent_disk_buffer_bytes: 300000000`. With `drop_newest` a smaller cap (e.g. 64–100 MiB) is safe because alerts will fire when drops occur.
3. Add alerts (see §4).

---

## 2. Root Cause — FreeBSD Routers and `mail` Absent from Loki

### What happened

FreeBSD routers (`cr1-nl1`, `cr1-de1`, `cr1-ch1`) and OpenBSD `mail` forward logs via native `syslogd(8)` `@@host:6514` TCP to the Vector aggregator on `log`. The aggregator has an `[sources.openbsd_syslog]` source on `::6514/tcp`.

There are **two independent problems**:

#### 2a. Missing firewall rule for `cr1-ch1` on `log:6514`

- File: `ansible/inventory/host_vars/log.yml`
- `firewall_extra_rules` has 6514 rules for `cr1-nl1` and `cr1-de1` but **omits `cr1-ch1`**.
- Because of this, `cr1-ch1` cannot even reach `log:6514`. Its syslog TCP SYNs are dropped at `log`'s nftables input chain.
- Verifiable by checking `ansible/inventory/host_vars/log.yml`: search for `6514`; only `cr1_nl1` and `cr1_de1` appear.

**Fix:** Add:

```yaml
  - { proto: tcp, dport: 6514, src: "{{ peers.cr1_ch1.loopback }}", comment: "syslog (TCP) from cr1-ch1" }
```

Also update `docs/network-flows.md` to note that the rule is live once applied.

#### 2b. Syslog enrich transform hardcodes `role = "mail"` for ALL syslog sources

- File: `ansible/roles/logs/templates/vector-aggregator.toml.j2`
- Transform `[transforms.syslog_enrich]`:

```toml
[transforms.syslog_enrich]
type = "remap"
inputs = ["openbsd_syslog"]
source = '''
.host = string(.hostname) ?? "mail"
.role = "mail"
.service = string(.appname) ?? "syslog"
.level = string(.severity) ?? "info"
'''
```

The `openbsd_syslog` source also receives FreeBSD syslog (both forward to the same `::6514` listener). When a FreeBSD router sends syslog, `.hostname` is usually correct (e.g. `"cr1-nl1"`), so `.host` is okay. But `.role` is hardcoded to `"mail"` regardless of the sender. This means:

- FreeBSD router logs are incorrectly tagged `role="mail"`.
- In Grafana, `{host="cr1-nl1"}` returns nothing because `.host` might also be wrong if the router hostname is not exactly the inventory name.

**Fix:** Make the transform conditional on `.hostname`:

```vrl
.host = string(.hostname) ?? "unknown"
.role = "router"
if string(.hostname) ?? "" == "mail.as215932.net" || string(.hostname) ?? "" == "mail" {
    .role = "mail"
}
```

Or, better, match known router hostnames:

```vrl
.host = string(.hostname) ?? "unknown"
.role = "mail"
if starts_with(string(.hostname) ?? "", "cr1-") {
    .role = "router"
}
```

Because `cr1-ch1` was missing the firewall rule entirely, this bug was masked for `cr1-nl1` and `cr1-de1` as well (no one queried `role="mail"` looking for router logs).

#### 2c. `mail` absence

`mail` uses the same `[sources.openbsd_syslog]` path. If `.hostname` is empty for any reason, `.host` falls back to `"mail"`, which is okay. But if `mail` is also absent from Loki, the root cause is different (e.g. `syslog.conf.openbsd.j2` may not be applied, `syslogd` not restarted, or mail's outbound 6514 blocked). That needs separate on-host verification.

---

## 3. Buffer-Corruption Detail (`partial_write`)

Vector 0.55.0's disk buffer is susceptible to a partially-written record if the process crashes or the host loses power while an event is being serialized to disk. On the next start the reader task encounters:

```text
Error encountered during buffer read ... a data file contains a partially-written record.
error_code="partial_write" error_type="reader_failed"
```

With `when_full = "block"`, the topology deadlocks:
- The reader stops because it cannot parse the corrupt record.
- The writer stops because the buffer is full and it must block.
- The process stays up, the API stays healthy, and no logs flow.

With `when_full = "drop_newest"`, the corrupt-record deadlock is less likely to be fatal because new events are discarded rather than backing up the writer. However, the real fix is to ensure the corrupted record is skipped. Vector **did** skip it on restart (dropping 2 events), so the behavior is as-designed. The operational error was the failure mode being silent.

### Recommendation

- Switch agents to `"drop_newest"` (or `"drop_oldest"`) with a **much smaller cap** on small-root hosts.
- Do **not** increase the buffer size on `rtr` — it will only prolong the silent failure window.
- Document in `ansible/roles/logs/defaults/main.yml` that `"block"` should only be used on the aggregator or hosts with >10 GB root.

---

## 4. Required Alerts (Prometheus on `mon`)

Current rules in `configs/mon/prometheus-rules/logs-pipeline.yml` cover:
- `VectorDown` — aggregator scrape endpoint down.
- `LokiDown` — Loki /metrics down.
- `VectorBufferStalling` — aggregator buffer growing.
- `LokiCardinalityHigh` — stream count.
- `LokiDiskHigh` — node_exporter filesystem for `/var/lib/loki`.
- `LokiCompactorRetentionStuck` — retention not progressing.

There is **NO** alert for:
- A specific host failing to ship logs.
- Agent-side buffer drops.
- Absence of expected `host` labels in Loki streams.

Because Prometheus does not ingest Loki log data natively, an `absent`-style check must be done in **one of two ways**:

### Option A — Loki Ruler (preferred for syslog-only sources)

Loki has a built-in ruler that evaluates LogQL and sends alerts to Alertmanager. It is already partially configured in the `loki-config.yaml.j2` (`ruler:` section with local storage), but:
- No `alertmanager_url` is configured, so alerts have nowhere to go.
- No rule files are written to `/var/lib/loki/rules`.

**Implementation sketch:**

1. Add to `loki-config.yaml.j2`:
   ```yaml
   ruler:
     alertmanager_url: http://[2a0c:b641:b50:2::50]:9093  # mon Alertmanager
     ... (existing storage/ring config)
   ```
2. Add firewall rule on `mon` allowing `log` to reach `mon:9093`.
3. Write rule file to `/var/lib/loki/rules/infra/pipeline.yml`:
   ```yaml
   groups:
     - name: host-liveness
       rules:
         - alert: LogsAbsentHost
           expr: |
             absent(count_over_time({host="cr1-nl1"}[1h])) or
             absent(count_over_time({host="cr1-de1"}[1h])) or
             absent(count_over_time({host="cr1-ch1"}[1h])) or
             absent(count_over_time({host="mail"}[1h])) or
             absent(count_over_time({host="rtr"}[1h]))
           for: 2h
           labels:
             severity: critical
             source: loki-ruler
   ```
   (A better formulation is one alert per host, using templated LogQL.)
4. Note: when Loki is down, the absent expressions evaluate to 0 (no firing), so this alert depends on Loki being up. That is acceptable because `LokiDown` already pages when Loki itself is dead.

### Option B — Vector agent metrics scrape (preferred for agent hosts)

Agents currently do **not** expose Prometheus metrics externally. Their `[api]` binds to `127.0.0.1:8686` and there is no `[sinks.prometheus_metrics]` sink in `vector-agent.toml.j2`.

**Implementation sketch:**
1. Add to `vector-agent.toml.j2`:
   ```toml
   [sources.internal_metrics]
   type = "internal_metrics"

   [sinks.prometheus_metrics]
   type = "prometheus_exporter"
   inputs = ["internal_metrics"]
   address = "[::]:8686"
   ```
2. Open `mon → <host>:8686` in each host's `firewall_extra_rules`.
3. Add scrape jobs in `configs/mon/prometheus.yml` for each agent host:
   ```yaml
   - job_name: vector-agents
     static_configs:
       - targets:
           - "[2a0c:b641:b50:2::1]:8686"   # rtr
           - "[2a0c:b641:b50:2::10]:8686"  # dns
           # ... etc
   ```
4. Add Prometheus alert rules:
   ```yaml
   - alert: VectorAgentNoShip
     expr: increase(vector_component_sent_events_total{component_id="aggregator"}[5m]) == 0
     for: 1h
     labels:
       severity: critical
     annotations:
       summary: "Vector agent on {{ $labels.instance }} has sent 0 events for 1h"
   ```
   And for `drop_newest` mode:
   ```yaml
   - alert: VectorAgentDropping
     expr: increase(vector_component_dropped_events_total{component_id="aggregator"}[5m]) > 0
     for: 5m
     labels:
       severity: warning
     annotations:
       summary: "Vector agent {{ $labels.instance }} is dropping events (buffer full)"
   ```

Because FreeBSD routers do **not** run Vector, Option A (Loki ruler) is the only way to alert on them. Option B is still valuable for Linux agent hosts. Both can coexist.

### Option C — Quick win today

Without adding ruler infrastructure or agent metrics, a short-term mitigation is an **Icinga2 passive check or a cron-based canary**:
- A small script on `mon` that runs `curl -s "http://[::b0]:3100/loki/api/v1/query" ...` every 10 minutes and fails if `{host="rtr"}` has no entries in the last 2h.
- However, this is fragile and should be replaced by Option A or B.

---

## 5. Required Config Changes (checklist)

### `ansible/inventory/host_vars/log.yml`
- [ ] Add `cr1_ch1` firewall rule for dport 6514.

### `ansible/inventory/host_vars/rtr.yml`
- [ ] Change `logs_agent_disk_buffer_bytes` to a smaller value (e.g. `100000000`).
- [ ] Add `logs_agent_buffer_when_full: "drop_newest"`.

### `ansible/roles/logs/defaults/main.yml`
- [ ] Add `logs_agent_buffer_when_full: "drop_newest"` (default).
- [ ] Document that `"block"` should only be used on the aggregator / `log` host.

### `ansible/roles/logs/templates/vector-agent.toml.j2`
- [ ] Make `when_full = "{{ logs_agent_buffer_when_full }}"`.
- [ ] Optionally add a small comment warning about buffer deadlock.

### `ansible/roles/logs/templates/vector-aggregator.toml.j2`
- [ ] Fix `[transforms.syslog_enrich]` so `.role` is `"router"` for hostnames matching `cr1-*` / `rtr*`, `"mail"` for mail hostnames.
- [ ] Ensure `.host = string(.hostname) ?? "unknown"` (do not fall back to `"mail"`).

### `configs/mon/prometheus-rules/logs-pipeline.yml`
- [ ] Add `VectorAgentDropping` alert (works if agent metrics are scraped).
- [ ] Add `LogsAbsentHost` alert via Loki ruler (or at least document the gap).

### `ansible/roles/logs/templates/loki-config.yaml.j2`
- [ ] Add `alertmanager_url` to the `ruler:` section pointing at `mon:9093`.
- [ ] Add rule file for `LogsAbsentHost` on expected hosts.

### `ansible/inventory/host_vars/mon.yml` (or firewall role)
- [ ] Allow inbound 9093 from `log` if Alertmanager is bound to `127.0.0.1:9093` today. May require moving Alertmanager to the overlay IPv6 or adding a local reverse proxy.

### `docs/network-flows.md`
- [ ] Verify that `cr1-ch1` 6514 is listed and mark as live after the rule is applied.

---

## 6. Verification Steps (after apply)

1. **Firewall**
   ```bash
   # On log
   sudo nft list ruleset | grep 6514
   # Should show rules for cr1-nl1, cr1-de1, cr1-ch1, and mail.
   ```
2. **Aggregator**
   ```bash
   # On log
   sudo vector top
   # Should show nonzero `received` on `openbsd_syslog`.
   ```
3. **Loki query**
   ```text
   Grafana Explore → {host="cr1-ch1"}
   {host="cr1-nl1"}
   {host="cr1-de1"}
   {host="mail"}
   {host="rtr"}
   ```
   All should return recent lines.
4. **Buffer policy**
   ```bash
   # On rtr
   grep when_full /etc/vector/vector.toml
   # Expected: drop_newest
   ```
5. **Alerts**
   - Stop `vector` on a non-critical agent host for 10 minutes and confirm `VectorAgentNoShip` (or equivalent) fires.
   - Block syslog on a FreeBSD router for 10 minutes and confirm `LogsAbsentHost` fires.

---

## 7. Timeline ( reconstructed )

| Time (UTC) | Event |
|---|---|
| 2026-06-05 ~00:00 | `rtr` Vector agent starts after an unplanned reboot. |
| 2026-06-05 ~10:00 | Disk buffer fills (`vector_buffer_byte_size` = 300 MB). Sink deadlocked on `partial_write` record. Writer blocks. |
| 2026-06-05 – 2026-07-08 | **One month of silent log loss.** systemd unit `active`, API healthy. |
| 2026-07-08 22:51 | `cr1-ch1` routing incident occurs. No logs in Loki for forensics. |
| 2026-07-09 | Icinga `rtr!disk disk /` warning triaged. Manual Vector restart surfaces `partial_write` error. Buffer drains, disk clears. Incident #374 opened. |

---

## 8. Lessons

1. **A healthy process is not a healthy pipeline.** Process-level monitoring (systemd, `/health`) does not prove data is flowing. End-to-end liveness checks are required.
2. **`block` on a finite disk is a silent failure mode.** Prefer `drop_newest` with an alert over `block` with a disk-pressure alert. Disk-pressure is noisy and lags by hours or days.
3. **Firewall rules must be kept in sync with architecture docs.** `network-flows.md` listed `cr1-ch1:6514` but the live `log.yml` omitted it, suggesting a config drift during the `cr1-ch1` onboarding.
4. **Single-source syslog transforms must discriminate by sender.** Hardcoding `role = "mail"` for a multi-tenant syslog listener broke FreeBSD router discoverability.

---

## References

- `ansible/roles/logs/templates/vector-agent.toml.j2`
- `ansible/roles/logs/templates/vector-aggregator.toml.j2`
- `ansible/inventory/host_vars/log.yml`
- `ansible/inventory/host_vars/rtr.yml`
- `ansible/roles/logs/defaults/main.yml`
- `configs/mon/prometheus-rules/logs-pipeline.yml`
- `configs/mon/prometheus.yml`
- `docs/network-flows.md` (log host section)
- `docs/application-logging.md`

*This runbook was generated by the Engineering Loop tranche for issue #374. Manual operator review and an apply-run are required before the firewall, aggregator, and agent changes take effect.*
