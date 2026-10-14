# Runbook: rtr-bsd — parallel OpenBSD edge router (issue #14)

Goal: run a new OpenBSD edge router **in parallel** with live `rtr` (Debian)
so routing, DNS, NAT64, and DNAT services can be proved one by one and then
switched over **atomically**, with instant rollback. No in-place conversion,
no flag day.

Decision log (settle the open questions from issue #14):

- **Superseded 2026-10-14: OpenBSD base `bgpd(8)` + `ospf6d(8)`, not FRR.**
  FRR is not packaged for OpenBSD 7.8. Svag decided to use the base daemons;
  `configs/rtr-bsd/frr.conf` was translated 1:1 into `bgpd.conf` +
  `ospf6d.conf` (same mesh, next-hop-self, originated set, ch1 200 > nl1 90 >
  de1 80 ladder, transit-free, lo0-only OSPF6). Pinned by
  `tests/iac/test_rtr_bsd_openbgpd.py`. Burn-in safety: every neighbor is
  `down`, and announcements carry localpref 50 / MED 500 (< rtr's 100) so
  cores and rtr never prefer rtr-bsd. Cutover: drop `down`, then the
  localpref/MED override. The original rationale follows for history.
- ~~**FRR on OpenBSD, not OpenBGPD/OpenOSPFD.**~~ The fleet is FRR-only and the
  IaC contracts pin FRR syntax; a second BGP dialect doubles review cost for
  zero forwarding benefit. The VRF/systemd-networkd/Jool pain lives below FRR.
- **Stateful NAT64 via pf `af-to`, dedicated translation IP.** Per the #23
  lesson: never share the router's egress IP with the translation pool. The
  cutover orders a second OVH failover IPv4 for NAT64/af-to; rtr's
  46.105.40.223 keeps serving DNAT until the atomic switch.
- **rdomain, not VRF leaks.** OpenBSD `rdomain(4)` replaces table-200/IP-rule
  plumbing if interface separation is ever needed; Phase 1 needs none (single
  table, like the FreeBSD cores).

## Addressing (all distinct from live rtr — no conflicts by construction)

| Function | rtr (live) | rtr-bsd (new) |
|---|---|---|
| Infra (xenbr-infra) | `2a0c:b641:b50:2::1/64` | `2a0c:b641:b50:2::2/64` |
| Customer (xenbr-vm) | `2a0c:b641:b51::1/48` | `2a0c:b641:b51::2/48` |
| Overlay loopback | `2a0c:b641:b50::d/128` | `2a0c:b641:b50::e/128` |
| OVH underlay (xenbr0) | `2001:41d0:303:48a::2/64` | `2001:41d0:303:48a::3/64` |
| BGP router-id | 0.0.0.13 | 0.0.0.14 |
| Failover IPv4 | 46.105.40.223 (DNAT+NAT64) | none in Phase 1 |

New WireGuard links (fresh /127s, clear of the ff00–ff08 core mesh):

| Link | rtr-bsd side | Core side | Overlay /127 |
|---|---|---|---|
| rtr-bsd wg0 ↔ cr1-nl1 new iface | listen 1343 | listen 1343 (`wg5.conf`) | `ff10::/127` |
| rtr-bsd wg1 ↔ cr1-de1 new iface | listen 1344 | listen 1343 (`wg3.conf`) | `ff11::/127` |
| rtr-bsd wg2 ↔ cr1-ch1 new iface | listen 1343 | listen 1343 (`wg3.conf`) | `ff12::/127` |

## Phase 0 — merge this branch (done when the PR lands)

Committed in-tree, all covered by CI (`render-check`, `iac-tests`):

- `ansible/inventory/hosts.yml`: `rtr-bsd` in `openbsd` + `routers`.
- `group_vars/all.yml`: `peers.rtr_bsd`/`rtr-bsd` (ipv6/loopback/underlay).
- `host_vars/rtr-bsd.yml`: pf router profile, monitoring, logs, flows.
- `configs/rtr-bsd/{frr.conf,wg0.conf,wg1.conf,wg2.conf}`.
- `configs/cr1-{nl1,de1,ch1}/frr.conf`: `::e` iBGP neighbor (idle until
  rtr-bsd boots — FRR integrated reload does not flap existing sessions;
  the `at(1)` watchdog self-heals a bad push).
- `configs/cr1-nl1/wg5.conf`, `cr1-de1/wg3.conf`, `cr1-ch1/wg3.conf` (peer
  stubs; keys filled at build).
- `configs/rtr/frr.conf`: `::e` iBGP neighbor (plain, no local-pref map —
  rtr-bsd is a fellow edge router, not an egress core).
- `host_vars/cr1-{nl1,de1,ch1}.yml`: `pf_wg_ports` += 1343.
- `roles/frr/vars/OpenBSD.yml` (new), `roles/firewall/vars/OpenBSD.yml`
  (profile selection moved to per-host `firewall_pf_template`; mail pinned
  to `pf-service.conf.j2` with byte-identical render).
- `configs/dom0/network-setup.sh`: NDP proxy for `48a::3`.
- `autoinstall/openbsd-rtr-bsd.conf`: installer answers.
- Tests: `test_inventory_schema.py` (routers set, loopbacks),
  `test_frr_static.py` (5-way mesh, rtr-bsd LP ladder mirrors rtr,
  /48 origination on both edge routers).

Deploy order for the **already-running** fleet (non-disruptive, watchdog
guarded — `frr` playbook runs `serial: 1`):

1. `dom0`: re-run `configs/dom0/network-setup.sh` (adds the `48a::3` proxy).
2. `firewall` on cr1-nl1/de1/ch1 + log (opens UDP 1343 + syslog from ::2).
3. `frr` on cr1-nl1/de1/ch1, then rtr (adds the idle `::e` neighbors).

Verify: `vtysh -c 'show bgp ipv6 unicast summary'` on each core shows the
`::e` neighbor in `Idle` (no opens yet) and all established sessions
undisturbed.

## Phase 1 — build rtr-bsd (human steps on dom0/XO)

1. Create the VM (mail path precedent, `docs/deployment.md` Phase 7b):
   2 vCPU / 2 GiB / 12 GiB, OpenBSD 7.x ISO, 4 VIFs **in order**
   mgmt → infra → vm → wan. Serve `autoinstall/openbsd-rtr-bsd.conf` as
   `install.conf`. No OVH virtual MAC needed (no failover IPv4 in Phase 1;
   v6 behind dom0's NDP proxy works with the Xen auto-MAC).
2. Post-install `/etc/hostname.*`:
   - `hostname.xnf0`: `inet6 autoconf` (mgmt link-local).
   - `hostname.xnf1`: `inet6 2a0c:b641:b50:2::2 64` + default route via
     `2a0c:b641:b50:2::1` (live rtr — burn-in gateway).
   - `hostname.xnf2`: up, no address (FRR assigns `b51::2/48`).
   - `hostname.xnf3`: `inet6 2001:41d0:303:48a::3 64` + default via
     `2001:41d0:303:48a::1` (dom0).
   - `doas.conf` for `svag` (same as mail), `pkg_add python3 frr wireguard-tools unbound`.
3. Generate WG keys on rtr-bsd + each core's new iface; fill the
   `<*_PUBLIC_KEY>` / `<PRIVATE_KEY>` placeholders in the committed
   `wg*.conf` files and deploy (cores: `/usr/local/etc/wireguard/` +
   `wg-quick`/`wireguard-go` up; exact command follows the existing
   core practice — no Ansible wireguard role exists yet).
4. OpenBSD specifics to confirm on the live host, then codify:
   - `roles/frr/vars/OpenBSD.yml`: `frr-reload` wrapper + pythontools pkg
     name (fall back to `rcctl reload frr` if the port lacks it).
   - FRR zebra address assignment on `xnf*` (else move addresses to
     `hostname.xnf*` and keep FRR interface stanzas address-free).
   - `pass ... proto ospf` parses under `pfctl -nf` (else `proto 89`).
   - `frr_exporter` availability on OpenBSD (else drop
     `prom_instance_frr` until packaged).
5. Apply in order, each with its watchdog: `firewall` → `frr` →
   `monitoring` (registers `rtr-bsd` on mon) → `logs` (syslog forward) →
   Unbound DNS64 (copy `configs/rtr/unbound/*`, re-point `interface:` to
   `2a0c:b641:b50:2::2`; no VRF confinement needed — see host_vars note).
6. Rejoin the drift sweep: remove `:!rtr-bsd` from the default limit in
   `scripts/ci/check-drift.sh` and delete
   `test_unbuilt_parallel_router_stays_out_of_the_default_sweep` in
   `tests/iac/test_drift_detection.py` — same commit that first boots the
   VM, so nightly drift starts covering rtr-bsd immediately.

## Phase 2 — burn-in (≥ 1 week as 5th iBGP router)

- iBGP: all four `::e` sessions Established; `show bgp ipv6 unicast`
  on cores shows rtr-bsd paths alongside rtr paths.
- OSPF6: `::e` loopback redistributed; `show ipv6 ospf6` neighbors on wg0-2.
- Egress agreement: rtr-bsd's LP ladder mirrors rtr's — spot-check that a
  traceroute sourced from `::2` prefers ch1 exactly like one from `::1`.
- DNS: point a scratch VM at `2a0c:b641:b50:2::2`; AAAA + DNS64 synthesis
  must match rtr's answers record-for-record.
- Isolation canary: from `ci-pr`, `mon:9100` via `::2` as gateway must time
  out while DNS to `::2` works.
- Icinga `rtr-bsd` host green (ping6, ssh, node, frr via the
  `has_frr`/`prom_instance_frr` apply rules).

## Phase 3 — atomic cutover (per service, each instantly reversible)

1. **Gateway addresses**: move `2a0c:b641:b50:2::1` and `2a0c:b641:b51::1`
   onto rtr-bsd (`ifconfig xnf1 alias ...` + gratuitous NA), remove from
   rtr. Rollback: move them back. Infra VMs keep working without any guest
   change — this is the atomicity the parallel build buys.
2. **DNAT**: order a second OVH failover IPv4 for rtr-bsd (or move
   46.105.40.223 with its vMAC — same VIF-MAC procedure as `deployment.md`
   Phase 3b); add the pf `rdr` rules sketched as CUTOVER DELTA in
   `host_vars/rtr-bsd.yml`; shift DNS delegation/peer expectations.
3. **NAT64**: dedicated translation IP (never the router egress IP, #23);
   pf `af-to` + Unbound DNS64 already in place; flip the anycast by moving
   the DNS64 listener expectation from `::1` to the moved gateway address.
4. **Decommission**: remove rtr from `routers`, retire its Host object,
   close issue #14. rtr-bsd renames are NOT required (DNS `rtr` can point
   at the new box; the `-bsd` suffix becomes history).

## Rollback at every step

- Pre-cutover: stop rtr-bsd — production never depended on it (`::1`,
  DNAT, NAT64 all still on rtr; `::e` sessions Idle).
- Post-cutover per service: move the address back to rtr (gateway addrs,
  failover IP + vMAC). rtr's configs are untouched by this migration
  except the additive idle `::e` neighbor.

## Build log — 2026-10-14 (Phase 1, partial)

Built from the `OpenBSD 7.8 cloud-init` template on xcp01 (VM `rtr-bsd`,
uuid `ad349834-dabc-288c-891b-f1c5b4f3eb61`, 2 vCPU / 2 GiB / 12 GiB, VIFs
mgmt → infra → vm → eth0 as xnf0..3, NoCloud seed `rtr-bsd-seed.iso`).
dom0 NDP proxy for `48a::3` applied (`configs/dom0/network-setup.sh`).

Findings that change the plan above:

- **FRR is not packaged for OpenBSD 7.8** (`pkg_info -Q frr` is empty; only
  `quagga-1.2.4p3`). The "FRR on OpenBSD" decision needs revisiting:
  build FRR from ports/source, or switch rtr-bsd to base `bgpd(8)`/`ospf6d(8)`.
  Blocked pending a decision; FRR is NOT running on rtr-bsd.
- **DAD vs dom0 NDP proxy**: dom0 proxies `48a::3` on xenbr0, the same bridge
  the VM sits on, so OpenBSD marked the address `duplicated`. Fixed with
  `net.inet6.ip6.dad_count=0` in `/etc/sysctl.conf`.
- **Template root is 2 GiB (94% full)**. The 12 GiB VDI's extra space is a
  separate `sd0d` FFS mounted on `/usr/local` (contents copied over).
- **One default route only**, via dom0 `2001:41d0:303:48a::1` on xnf3 (the
  WG underlay). The two defaults in step 2 conflict; xnf1 holds `::2/64`
  on-link with no gateway. Infra-sourced traffic from off-link hosts therefore
  returns asymmetrically via WAN (SSH to `::2` through rtr times out); manage
  the box via mgmt link-local from dom0 until the overlay is up.
- WG keys generated on-host (`/etc/wireguard/wg{0,1,2}.key`, 0600). Public
  keys are filled into the core-side stubs in this PR.

### 2026-10-14 (later): bgpd/ospf6d deployed

- `/etc/bgpd.conf`, `/etc/ospf6d.conf` (0600) installed from `configs/rtr-bsd/`;
  `bgpd -n` / `ospf6d -n` OK; both `rcctl enable`d and running. All four
  neighbors `Idle` (administratively down).
- `/etc/hostname.lo0` adds `::e/128` and the `/44` blackhole;
  `hostname.xnf2` holds `b51::2/48`; `hostname.wg{0,1,2}` create the WG
  interfaces (key from `/etc/wireguard/wgN.key`, port 1343/1344/1345, overlay
  /127, link-local `fe80::e` for ospf6d). No peers configured yet.
- `net.inet6.ip6.forwarding=1`.
