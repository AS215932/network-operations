---
created: 2026-06-14T16:47:37.890Z
source: pi-plan-mode
status: implemented
---

# AS215932 NETCONF/YANG Adoption Plan for FRRouting

## Summary

Use YANG immediately for model-driven validation, structured diffing, and read-only verification of AS215932 FRR routers. Treat production NETCONF writes as a later, gated capability: prove them in lab first, do not introduce custom FRR packages in production, and keep `configs/<host>/frr.conf` canonical until renderer parity is proven.

## Locked Decisions

- **Goal:** safer FRR provisioning and NetOps QoL.
- **Source of truth:** committed `configs/<router>/frr.conf` remains canonical.
- **Router state:** observed state only, never authoritative.
- **Adoption:** lab first, then read-only production pilot.
- **Northbound target:** standard NETCONF over SSH via Netopeer2/sysrepo if feasible.
- **Production packages:** no custom FRR packages initially.
- **Access:** any production NETCONF endpoint must be overlay-only TCP/830, firewall-restricted to `ci`, `noc`, and ops sources.
- **Structured intent first area:** policy primitives: prefix-lists, AS-path filters, route-maps, and route-map neighbor attachments.

## Key Research Findings

- FRR has native YANG models and a management daemon, `mgmtd`.
- `mgmtd` stores config in YANG-shaped candidate/running/operational datastores and exposes local frontend/backend Unix sockets, not a standard remote NETCONF server by itself.
- Standard NETCONF for FRR generally requires FRR built with `--enable-sysrepo`, FRR YANG modules loaded into sysrepo, and a NETCONF server such as Netopeer2.
- Public Debian/FreeBSD packaging inspected does not appear to enable the sysrepo plugin by default; verify live routers during implementation.
- Current repo deploy path is already safe: Ansible stages `frr.conf`, validates, backs up, schedules rollback watchdog, reloads via FRR integrated reload, then soft-clears BGP.
- `cr1-ch1` has `mgmtd` listed in committed `rc.conf`; no repo evidence of NETCONF/sysrepo/Netopeer2 on production routers.
- No current firewall allowance exists for TCP/830.

## Implementation Steps

1. [x] Record the NETCONF/YANG architecture decision and production gates. See `docs/netops/netconf-yang.md`.
2. [x] Add a router YANG/capability inventory and read-only audit role. See `ansible/roles/frr_yang/`, `ansible/playbooks/frr-yang.yml`, and router `frr_*` inventory metadata.
3. [x] Add normalized FRR semantic extraction and structured diff tooling. See `scripts/netops/frr_semantic.py` and `ansible/generated/*/frr-semantic.json`.
4. [x] Extend static tests to cover all four routers and policy invariants. See `tests/iac/test_frr_static.py` and router loopback coverage in `tests/iac/test_inventory_schema.py`.
5. [x] Build a trusted Containerlab NETCONF/YANG lab. See `tests/iac/containerlab/as215932-netconf.clab.yml`, `tests/iac/containerlab/netconf/`, `tests/iac/containerlab/check_netconf_yang.py`, and `scripts/ci/containerlab-netconf-yang-test.sh`.
6. [x] Add read-only production drift/audit workflow. See `.github/workflows/frr-yang-audit.yml`.
7. [x] Add disabled-by-default production NETCONF endpoint scaffolding. See router `frr_netconf_*` defaults, firewall `_netconf_endpoint_*` partials, and disabled endpoint tests.
8. [x] Introduce structured Git intent for policy primitives behind parity gates. See `configs/frr-policy-intent.yml`, `scripts/netops/render_frr_policy.py`, `ansible/generated/*/frr-policy.*`, and parity tests in `tests/iac/test_frr_static.py`.

## Detailed Design

### 1. Documentation and ADR

Add `docs/netops/netconf-yang.md` covering:

- FRR native YANG vs IETF/OpenConfig models.
- Why production `frr.conf` remains canonical.
- Why production NETCONF writes are blocked until lab proves:
  - advertised NETCONF capabilities,
  - candidate/validate/commit behavior,
  - rollback behavior,
  - persistence behavior,
  - no BGP session disruption.
- Explicitly out of scope for first implementation:
  - making live routers authoritative,
  - custom FRR production packages,
  - public NETCONF exposure,
  - full OpenConfig translation,
  - replacing the existing `frr` Ansible role.

### 2. Inventory Additions

Add router metadata:

- In `ansible/inventory/group_vars/routers.yml`:
  - `frr_yang_audit_enabled: true`
  - `frr_netconf_endpoint_enabled: false`
  - `frr_netconf_write_enabled: false`
  - `frr_netconf_port: 830`
  - `frr_netconf_allowed_sources_v6`:
    - `{{ peers.ci.ipv6 }}/128`
    - `{{ peers.noc.ipv6 }}/128`
    - `{{ ops_prefix_v6 }}`
- In each router host var:
  - `frr_version_expected`
  - `frr_yang_profile`
  - `frr_vrf_context`: `overlay` for `rtr`, `default` for `cr1-*`.

### 3. Read-only Audit Role

Add role `ansible/roles/frr_yang/`.

Read-only audit tasks must run with `changed_when: false` and collect:

- `vtysh -c 'show version'`
- `vtysh -c 'show bgp ipv6 summary json'`
- `vtysh -c 'show configuration running json bgpd'`
- `vtysh -c 'show configuration running json staticd'`
- `vtysh -c 'show configuration running json zebra'`
- `vtysh -c 'show configuration running json ospf6d'`
- `vtysh -c 'show mgmt backend-adapter all'`, allowed to fail if unsupported.
- Package/capability evidence:
  - Debian: `dpkg-query -W frr`
  - FreeBSD: `pkg info -x frr`
  - check for sysrepo plugin files/modules.

Write live artifacts under:

`ansible/generated/frr-yang-snapshots/<timestamp>/<host>/`

Add this path to `.gitignore`.

### 4. Structured Diff Tooling

Add `scripts/netops/frr_semantic.py`.

Inputs:

- `configs/<host>/frr.conf`
- optionally live audit JSON artifacts.

Outputs stable normalized JSON:

`ansible/generated/<host>/frr-semantic.json`

Normalize:

- hostname/version/router-id,
- VRFs,
- BGP ASN and AFs,
- neighbors and route-map attachments,
- prefix-lists,
- AS-path access-lists,
- route-maps,
- advertised networks,
- interface IPv6 addresses,
- OSPF6 router IDs/passive/link config.

Use this for structured diffs before attempting true NETCONF writes.

### 5. Static Tests

Update/add tests under `tests/iac/`:

- Include `cr1-ch1` in FRR static coverage.
- Derive router list from inventory instead of hardcoding three routers.
- Assert all routers have unique router IDs.
- Assert iBGP full mesh among `::a`, `::b`, `::c`, `::d`.
- Assert all external eBGP neighbors have inbound and outbound route-maps.
- Assert `AS215932v6-out` only permits `2a0c:b641:b50::/44`.
- Assert route-maps reference existing prefix/as-path lists.
- Assert host `frr_version_expected` matches the `frr version` line.
- Assert no production host has `frr_netconf_write_enabled: true`.

### 6. NETCONF/YANG Lab

Add trusted-only lab files:

- `tests/iac/containerlab/as215932-netconf.clab.yml`
- `tests/iac/containerlab/netconf/Dockerfile`
- `scripts/ci/containerlab-netconf-yang-test.sh`
- `tests/iac/containerlab/check_netconf_yang.py`

Lab image behavior:

- Build FRR pinned to the selected lab version with:
  - `--enable-sysrepo`
  - `--enable-config-rollbacks`
- Install FRR YANG modules into sysrepo.
- Run Netopeer2 as NETCONF server.
- Run `zebra`, `bgpd`, `ospf6d`, `staticd` with sysrepo support.
- Keep this image lab-only.

Lab assertions:

- NETCONF `<hello>` succeeds.
- RFC 6022 `/netconf-state/capabilities` is readable.
- `get-schema` works for FRR YANG modules.
- `get-config` returns BGP/policy data.
- Candidate lock/validate/commit works in lab.
- Abort/rollback restores original policy.
- BGP sessions remain established after lab NETCONF candidate commit.

Add optional workflow job gated by repo var:

`ENABLE_NETCONF_YANG_TESTS=true`

Do not run this on untrusted PRs.

### 7. Production Read-only Workflow

Add `.github/workflows/frr-yang-audit.yml`.

Run on:

- manual dispatch,
- nightly after existing drift detection, or as a separate schedule.

Behavior:

- SSH from `ci` runner.
- Run `ansible/playbooks/frr-yang.yml --tags audit`.
- Upload artifacts.
- Summarize:
  - FRR versions,
  - mgmtd availability,
  - sysrepo availability,
  - NETCONF availability,
  - schema/profile mismatch,
  - semantic drift between committed config and live running JSON.

No production config writes, no daemon restarts, no port changes.

### 8. Disabled Production NETCONF Endpoint Scaffolding

Add scaffolding only; default remains disabled.

When `frr_netconf_endpoint_enabled: true` for a host:

- Configure NETCONF server to listen only on overlay/loopback address.
- Open TCP/830 only from:
  - `peers.ci.ipv6`,
  - `peers.noc.ipv6`,
  - `ops_prefix_v6`.
- Never expose on underlay/public interfaces.
- Add read-only NACM/sysrepo access first.
- Require a separate PR to enable per host.

Production write enablement remains blocked unless all are true:

- lab NETCONF write test is green,
- target advertises `:candidate` and `:validate`,
- rollback/confirmed-commit strategy is proven,
- semantic diff after commit equals committed `frr.conf`,
- Icinga pre/post snapshots are clean,
- human approves production gate.

### 9. Structured Intent for Policy Primitives

Add `ansible/inventory/group_vars/routers.yml` or a dedicated policy file for common policy intent.

First generated sections:

- `ipv6 prefix-list AS215932v6-out`
- AS-path access-list `1`
- cr1-de1 special AS24961 local-pref rule
- `TRANSIT-IN`
- `TRANSIT-OUT`
- `TRANSIT-OUT-PREPEND-3X`
- `IXP-IN`
- `IXP-OUT`
- neighbor route-map attachments

Do not generate yet:

- interfaces,
- VRFs,
- static routes,
- OSPF6,
- base BGP neighbor definitions,
- WireGuard config.

Parity gate:

- Generator must reproduce current committed policy blocks byte-for-byte, modulo whitespace/comments explicitly normalized by tests.
- Only after parity, add generated block markers to `frr.conf`.
- Existing `frr` role remains deploy path.

## Rollout Order

1. Implement docs, metadata, static tests.
2. Add read-only audit role and run against `rtr`.
3. Extend to `cr1-ch1`, then `cr1-de1`, then `cr1-nl1`.
4. Build and validate NETCONF/YANG lab.
5. Add nightly read-only audit.
6. Add disabled endpoint scaffolding.
7. Enable read-only NETCONF endpoint on `rtr` only if package support exists without custom production FRR.
8. Consider production writes only in a future PR after all gates pass.

## Acceptance Criteria

- No production router config changes occur during initial implementation.
- `configs/<host>/frr.conf` remains canonical.
- All four routers are covered by static FRR tests.
- Read-only audit artifacts are produced for each router.
- Drift report clearly shows canonical vs observed differences.
- NETCONF lab proves schema discovery and candidate commit/rollback.
- Production TCP/830 remains closed unless explicitly enabled per host.
- `frr_netconf_write_enabled` is false everywhere.

## Operational Notes

- Continue using current `frr` Ansible role for production applies.
- Treat NETCONF/YANG initially as validation, diff, and verification infrastructure.
- If stock FRR packages cannot expose sysrepo/NETCONF safely, stop at read-only YANG audit plus lab NETCONF; do not introduce custom production packages.
