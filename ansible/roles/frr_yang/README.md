# frr_yang role

Read-only FRRouting YANG/NETCONF capability audit.

This role does **not** configure FRR, sysrepo, Netopeer2, or NETCONF endpoints.
Production desired state remains the committed `configs/<host>/frr.conf` files
and the existing `frr` role remains the apply path. Endpoint inventory knobs are
present only as disabled-by-default scaffolding for a later, explicitly gated
change.

## What it collects

On routers with `frr_yang_audit_enabled: true`, the role captures:

- FRR version and selected running-config JSON views via `vtysh`;
- BGP IPv6 summary, plus a VRF-specific summary when `frr_vrf_context` is not
  `default`;
- `mgmtd` backend-adapter evidence when supported;
- OS package evidence for FRR/sysrepo/Netopeer2/libyang;
- sysrepo module evidence when `sysrepoctl` exists;
- TCP/830 listener evidence;
- FRR YANG/module path evidence.

All commands are read-only and use `failed_when: false` so unsupported features
are captured as audit evidence.

## Artifacts

Artifacts are written on the controller under:

```text
ansible/generated/frr-yang-snapshots/<timestamp>/<host>/
```

That path is ignored by Git because it contains observed runtime state, not
desired state.

## Disabled endpoint scaffolding

Routers inherit:

- `frr_netconf_endpoint_enabled: false`
- `frr_netconf_write_enabled: false`
- `frr_netconf_port: 830`
- `frr_netconf_allowed_sources_v6` limited to CI, NOC, and ops sources.

When a future PR explicitly enables `frr_netconf_endpoint_enabled` for one host,
the firewall templates can render a restricted TCP/830 allow. This role still
will not install or start Netopeer2/sysrepo.

## Usage

```bash
cd ansible
ansible-playbook playbooks/frr-yang.yml --tags audit --limit rtr
```
