---
name: monitoring-onboarding
description: The AS215932 monitoring three-step — every host/service gets node_exporter + Icinga + Prometheus scrape from day one.
triggers: [adding a host, adding a service that needs probes]
---

# Monitoring onboarding (three-step)

Every server MUST have monitoring from day one. The `monitoring` role
(`ansible/roles/monitoring/`, playbook `ansible/playbooks/monitoring.yml`)
is the single entry point.

## Workflow

1. **Flows — `docs/network-flows.md`.** Open `mon → host:9100` for the
   node_exporter scrape (plus any service-specific probe ports), following
   the `firewall-change` skill.
2. **Host vars — `ansible/inventory/host_vars/<host>.yml`.** Set
   `monitoring_register: true` ONLY for hosts not already in the legacy
   `/etc/icinga2/conf.d/hosts/{infra-vms,routers,dom0}.conf` — duplicates
   fail the icinga2 reload. Add `monitoring_extra_services` for
   service-aware probes (DNS SOA, TLS validity, TCP port) as one
   `object Service` block each.
   *Checkpoint: confirmed the host is not in the legacy conf files before
   setting `monitoring_register`.*
3. **Prometheus — `/etc/prometheus/prometheus.yml` on mon** (mirrored at
   `configs/mon/prometheus.yml`). Add the host to the right
   `static_configs` job (`node-infra`, `node-routers`,
   `node-offsite-ns`, …). The role does not manage this file yet — keep the
   repo mirror and the change in sync.

Applying (`--tags apply -e '{"monitoring_apply":true}' --limit <host>`) and
the prometheus reload are operator actions, out of scope for the loop. Live
deploys bracket with Icinga snapshots before and after — never skipped
without a recorded emergency reason.

## Anti-rationalization

| Excuse | Rebuttal |
|---|---|
| "Monitoring can follow in a later PR" | Day-one rule. An unmonitored host is an outage you find by hand. |
| "Register it everywhere to be safe" | Duplicate Host objects fail the icinga2 reload fleet-wide. Check the legacy confs first. |
| "The generic checks are enough" | Service-aware probes (SOA, TLS, port) are what catch the real regressions; add them when a service is the point of the host. |

## Exit criteria

- Flow row for the scrape, host_vars monitoring config, and the
  prometheus.yml mirror change are all in the same diff.
- No duplicate Icinga Host object is possible (legacy confs checked).
- Service-specific probes exist for every service the change introduces.
