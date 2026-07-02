# Flow Weathermap Runbook

This is the lightweight flow stack for AS215932. It keeps raw sampled router
flows for traffic forensics while Grafana on `mon` remains the primary NOC
weathermap surface.

## Stack

- Collector VM: `flow` at `2a0c:b641:b50:2::110`.
- Capture daemons: `nfcapd` on UDP/2055 and UDP/4739, `sfcapd` on UDP/6343.
- Raw-flow UI: nfsen-ng over internal HTTP on `flow`.
- Exporters: `softflowd` on `rtr`, `cr1-nl1`, `cr1-de1`, and `cr1-ch1`.
- Default export: sampled NetFlow v9 to `[2a0c:b641:b50:2::110]:2055`.

## Deploy

Render review artifacts:

```bash
cd ansible
ansible-playbook playbooks/flow.yml --tags validate --connection=local --skip-tags snapshot
```

Apply the collector after the VM exists:

```bash
cd ansible
ansible-playbook playbooks/firewall.yml --tags apply -e '{"firewall_apply":true}' --limit flow
ansible-playbook playbooks/monitoring.yml --tags apply -e '{"monitoring_apply":true}' --limit flow
ansible-playbook playbooks/logs.yml --tags apply -e '{"logs_apply":true}' --limit flow
ansible-playbook playbooks/flow.yml --tags collector,apply -e '{"flow_apply":true}' --limit flow
```

Apply router exporters one router at a time:

```bash
cd ansible
ansible-playbook playbooks/flow.yml --tags exporters,apply -e '{"flow_exporter_apply":true}' --limit cr1-ch1
```

## Issue 351 Workflow

For CDN egress regressions, use the flow stack as evidence alongside BGP state:

- Check FRR best path/local-pref for Fastly AS54113 or Cloudflare AS13335 from the affected router.
- In nfsen-ng, inspect `netflow` for the relevant time window and filter by CDN endpoint or AS-derived prefix when known.
- Compare interface/source volume before and after a route-map or local-pref change.
- Use Grafana Canvas on `mon` for the high-level topology/link view; use nfsen-ng only for drill-down.

## Acceptance Checks

- `systemctl status flow-nfcapd-netflow flow-nfcapd-ipfix flow-sfcapd-sflow nfsen-ng` is healthy on `flow`.
- `nfdump -R /var/nfdump/profiles-data/live/netflow -s srcip/bytes -n 10` returns router flow records after exporters start.
- `systemctl status softflowd-*.service` is healthy on `rtr`; `service softflowd_* status` is healthy on FreeBSD routers.
- `flow!nfsen-ng-http`, `flow!netflow-collector`, and `flow!node_exporter` are green in Icinga.
