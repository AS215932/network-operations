---
name: firewall-change
description: The AS215932 firewall three-step — flows doc, host_vars, re-render — for any change to who talks to whom on which port.
triggers: [opening or closing a port, adding a peer or service, moving a host]
---

# Firewall change (three-step)

`docs/network-flows.md` is the single source of truth for "who talks to
whom on which port". Every rule in `ansible/inventory/host_vars/*.yml`
traces back to a row there. The order below is mandatory.

## Workflow

1. **Spec first — `docs/network-flows.md`.** Add/remove/edit the row in the
   per-host inbound table and any cross-cutting flow entry. If it's not in
   this file, it must not be in a rule.
   *Checkpoint: the row exists before any YAML is touched.*
2. **Rule second — `ansible/inventory/host_vars/<host>.yml`.** Append/edit
   the matching `firewall_extra_rules` entry. Reference peers by name
   (`{{ peers.mon.ipv6 }}`), never literal addresses. New peers go into
   `ansible/inventory/group_vars/all.yml` under `peers:` first.
3. **Re-render and review.**
   `cd ansible && ansible-playbook playbooks/firewall.yml --tags validate
   --connection=local --skip-tags=snapshot`. Inspect the diff in
   `ansible/generated/<host>/{nftables.conf,pf.conf}` and commit it as part
   of the same change.
   *Checkpoint: the generated diff matches the intended flow row, nothing
   more.*

New hosts additionally: define in `ansible/inventory/hosts.yml`, add to
`peers:`, write `host_vars/<host>.yml`, document flows, re-render — then
follow `monitoring-onboarding`.

Applying to live hosts is **out of scope for the loop**: apply is gated on
the `apply` tag + `firewall_apply=true` via the runbook in
`docs/ansible.md`, with Icinga snapshots before and after.

## Anti-rationalization

| Excuse | Rebuttal |
|---|---|
| "It's a temporary rule, skip the flows doc" | Temporary rules become permanent. Row first, always. |
| "I'll hardcode the IP, the peer dict is overhead" | Literal addresses rot silently when hosts move. Peers by name only. |
| "Generated diff is huge, commit without reading" | The generated diff IS the change. Unread diff = unreviewed firewall. |

## Exit criteria

- Flow row, host_vars rule, and committed re-rendered artifacts all present
  in one diff, mutually consistent.
- `--tags validate` exits clean.
- No literal peer addresses introduced.
