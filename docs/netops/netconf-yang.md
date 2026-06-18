# NETCONF/YANG adoption for AS215932 FRRouting

## Decision

AS215932 will use NETCONF/YANG as a model-driven safety and visibility layer
around FRRouting, not as the production source of truth. The committed
`configs/<router>/frr.conf` files remain canonical until a later structured
intent renderer proves parity and is explicitly promoted.

Routers are observed state. They are never authoritative for desired routing
policy.

## Scope

Initial adoption is read-only and validation-focused:

- collect FRR/YANG/NETCONF capability evidence from routers;
- build normalized, structured views of committed and running FRR config;
- use those views for semantic diffing, CI checks, and drift reports;
- prove standard NETCONF behavior in a trusted lab before any production write
  path is considered.

The existing Ansible `frr` role remains the production apply path. It already
stages `frr.conf`, validates with `vtysh -C -f`, backs up current config,
schedules an `at(1)` rollback watchdog, reloads via FRR integrated reload, and
soft-clears BGP policy.

## FRR YANG, NETCONF, and model choices

FRRouting provides native FRR YANG models and the `mgmtd` management daemon.
`mgmtd` maintains YANG-shaped candidate, running, and operational datastores and
exposes local frontend/backend IPC sockets. By itself, `mgmtd` is not a remote
standard NETCONF server.

A standard NETCONF-over-SSH interface for FRR generally requires:

1. FRR built with sysrepo northbound support, for example `--enable-sysrepo`;
2. FRR YANG modules installed into sysrepo;
3. a NETCONF server such as Netopeer2 connected to sysrepo;
4. router daemons started with the required northbound plugin support.

The preferred northbound target is standard NETCONF over SSH using
Netopeer2/sysrepo **if stock packages make this feasible**. Production will not
adopt custom FRR packages during the initial rollout. If packaged support is not
available, production remains read-only via FRR CLI/YANG evidence while full
NETCONF write behavior stays lab-only.

Native FRR YANG models are the first practical target because they closely match
FRR behavior. IETF/OpenConfig models may be useful later for translation or
external interoperability, but full OpenConfig parity is out of scope for the
first implementation.

## Production source-of-truth rules

1. `configs/<router>/frr.conf` is the production deployment record.
2. Live router config, NETCONF running datastore, sysrepo contents, and FRR
   operational state are evidence only.
3. Any drift found in observed router state must be resolved by changing Git and
   applying through the approved pipeline, or by reverting the router to Git.
4. Structured Git intent may be introduced gradually for generated sections only
   after byte-for-byte or explicitly normalized semantic parity is proven.
5. The first eligible generated sections are policy primitives:
   - prefix-lists;
   - AS-path filters;
   - route-maps;
   - neighbor route-map attachments.

Interfaces, VRFs, static routes, OSPF6, WireGuard, and base BGP neighbor
structure remain handwritten until a later decision.

## Production NETCONF access policy

Any production NETCONF endpoint must be disabled by default. If later enabled
for a specific host, it must follow these rules:

- listen only on overlay/loopback addressing, never public underlay interfaces;
- use TCP/830 only;
- be firewall-restricted to approved management sources:
  - `peers.ci.ipv6`;
  - `peers.noc.ipv6`;
  - `ops_prefix_v6`;
- start as read-only access first;
- require an explicit per-host PR to enable;
- never bypass the existing Git/Ansible production gate.

Production TCP/830 must remain closed unless a router explicitly sets
`frr_netconf_endpoint_enabled: true` and the firewall role renders the matching
restricted allow rule.

## Production write gate

NETCONF writes to production are blocked until all of the following are true:

1. A trusted Containerlab NETCONF/YANG lab is green.
2. The target package stack advertises the required NETCONF capabilities,
   including candidate and validate behavior.
3. Candidate lock, validate, commit, abort, and rollback behavior has been
   demonstrated in lab.
4. A NETCONF-applied lab change leaves BGP sessions established.
5. The post-commit semantic diff matches the committed `frr.conf` intent.
6. Persistence behavior is understood: reboot/restart must not lose or fork
   intended FRR config.
7. Rollback behavior is documented and tested for the target OS/package stack.
8. Icinga pre/post snapshots are clean in the normal production apply bracket.
9. A human approves the protected `production` environment gate.
10. `frr_netconf_write_enabled` is explicitly enabled for the single target host
    in the PR being applied.

Until this gate is satisfied, NETCONF/YANG is used for read-only audit,
validation, structured diffing, and lab experimentation only.

## Explicit non-goals for first implementation

- Making live routers authoritative.
- Replacing `configs/<router>/frr.conf` as canonical desired state.
- Installing custom FRR packages on production routers.
- Exposing NETCONF on public underlay interfaces.
- Enabling production NETCONF writes.
- Full OpenConfig translation.
- Replacing the existing Ansible `frr` role.
- Managing WireGuard, OS interface config, FreeBSD `rc.conf`, Debian networkd,
  nftables, or pf through NETCONF/YANG.

## Implementation phases

### Phase 1 — Read-only foundation

- Add router YANG/capability inventory metadata.
- Add a read-only `frr_yang` Ansible role and playbook.
- Collect version, package, mgmtd, sysrepo, NETCONF, and FRR running-config
  evidence.
- Store runtime snapshots outside tracked generated artifacts.
- Extend static tests to cover all routers and production write-disable
  invariants.

### Phase 2 — Structured diffing

`scripts/netops/frr_semantic.py` parses committed FRR CLI config into stable
JSON under `ansible/generated/<host>/frr-semantic.json`:

```bash
scripts/netops/frr_semantic.py --all
scripts/netops/frr_semantic.py --diff configs/rtr/frr.conf configs/rtr/frr.conf
```

The normalized data covers BGP instances and address families, neighbors,
route-map attachments, prefix-lists, AS-path lists, route-maps, interfaces,
static routes, VRFs, and OSPF6. Optional read-only audit artifacts can be
attached with `--audit-dir` for future committed-vs-observed drift reports.

- Parse committed `frr.conf` files into stable JSON.
- Normalize BGP, policy, interface, static route, OSPF6, and VRF-relevant data.
- Compare committed intent to read-only observed running state.
- Report semantic drift in CI/nightly audit without mutating routers.

### Phase 3 — Trusted NETCONF/YANG lab

The trusted lab lives under `tests/iac/containerlab/as215932-netconf.clab.yml`
and is launched by `scripts/ci/containerlab-netconf-yang-test.sh`. It builds the
lab-only image from `tests/iac/containerlab/netconf/Dockerfile`; that image is
not a production packaging path.

- Build a lab-only FRR image with sysrepo and Netopeer2.
- Validate RFC-style NETCONF schema discovery and capability reporting.
- Test candidate, validate, discard, commit, and cleanup flows.
- Assert BGP remains established after lab candidate commits.

### Phase 4 — Disabled production endpoint scaffolding

- Add inventory knobs for read-only NETCONF endpoint enablement.
- Add firewall scaffolding that only renders when explicitly enabled.
- Keep defaults disabled everywhere.

### Phase 5 — Structured intent pilots

The policy intent pilot is `configs/frr-policy-intent.yml`; render it with:

```bash
scripts/netops/render_frr_policy.py
```

It writes `ansible/generated/<host>/frr-policy.{json,conf}`. Static tests compare
that generated intent against committed `configs/<host>/frr.conf`; the generated
artifacts are parity evidence only and are not deployed.

- Introduce structured Git intent for policy primitives only.
- Prove renderer parity against current committed policy blocks.
- Promote generated sections only after review and passing parity tests.

## Acceptance checklist

- [ ] No production router config changes during initial implementation.
- [ ] `configs/<router>/frr.conf` remains canonical.
- [ ] All routers have static FRR policy coverage.
- [ ] Read-only audit artifacts can be produced for each router.
- [ ] Semantic drift can be reported without router mutation.
- [ ] NETCONF/YANG write behavior is proven in lab before production use.
- [ ] Production TCP/830 is closed unless explicitly enabled per host.
- [ ] `frr_netconf_write_enabled` is false everywhere by default.
