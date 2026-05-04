# rtr VRF routing

`rtr` (Debian 13, OVH FR) splits its interfaces between two routing
contexts:

| VRF       | Table | Interfaces                         | Purpose                          |
|-----------|-------|------------------------------------|----------------------------------|
| _default_ | main  | `enX0` (mgmt), `enX4` (wan)        | Underlay v6 + failover v4        |
| `overlay` | 200   | `enX2` (infra), `enX3` (vm), `wg0`, `wg1`, `lo-overlay` | AS215932 v6 + iBGP |

FRR runs `bgpd` inside the overlay VRF (`router bgp 215932 vrf overlay`,
two iBGP peers to cr1.nl1 / cr1.de1). Steady-state the kernel carries
~515k IPv6 routes — see [project_rtr_networkd_bgp memory] for why this
matters when restarting networkd.

VRF assignment is declarative in
[`configs/rtr/networkd/`](../configs/rtr/networkd/) — interfaces are in
the right VRF from boot, no runtime migration. The overlay netdev is
`05-overlay.netdev` (kind=vrf, table=200); each member interface
declares `VRF=overlay` in its `[Network]` block.

## IPv4 DNAT VRF leak

The infra subnet `10.0.2.0/24` lives on `enX2` (overlay VRF). External
v4 traffic arrives on `enX4` (default VRF) and is DNAT'd by nftables to
`10.0.2.{10,40,60}` (dns / proxy / vpn). To make this work without
duplicating the route in two tables, [`10-enX2.network`](../configs/rtr/networkd/10-enX2.network)
installs a symmetric pair of routing-policy rules:

```ini
[RoutingPolicyRule]
To=10.0.2.0/24
Table=200          # overlay
Priority=998

[RoutingPolicyRule]
From=10.0.2.0/24
Table=main
Priority=999
```

- **Forward (prio 998)**: Packet arrives at `enX4`, gets DNAT'd in
  PREROUTING (dst rewritten to `10.0.2.x`). Routing decision happens
  next; `enX4` is in default VRF so the kernel uses main table — but
  main has no route to `10.0.2.0/24`. The `To=10.0.2.0/24` rule pushes
  the lookup into table 200 where the address-derived route
  (`10.0.2.0/24 dev enX2`) is reachable.
- **Return (prio 999)**: Reply arrives at `enX2` (overlay VRF) with
  `src=10.0.2.x`, `dst=client_ip`. By l3mdev rule the kernel would
  consult table 200 — which has no v4 default. The `From=10.0.2.0/24`
  rule pushes the lookup into main, where `default via 193.70.32.254
  dev enX4` is. POSTROUTING reverse-NAT then rewrites src back to the
  failover IP before the packet leaves enX4.

This replaced `dnat-vrf-leak.service` (since-removed), which installed
`10.0.2.0/24 dev enX2` directly into MAIN. That left the route present
in **both** tables (main from the leak, table 200 from `[Address]
10.0.2.1/24`), causing asymmetric routing, ICMP redirect-to-self,
ARP cache flapping between rtr and the v4 backends, and ~18% UDP loss
on the DNAT path. The symmetric-rules approach has zero duplicate
routes; ARP stays REACHABLE; UDP DNAT passes 50/50.

## NAT64 VRF leak (separate mechanism)

NAT64 (Jool) runs in the **default** VRF only — it doesn't support
VRF. Overlay clients reach Jool via the IPv6 leak rules installed by
[`nat64-vrf-leak.service`](../configs/rtr/jool/nat64-vrf-leak.service):

```
ip -6 rule add from 2a0c:b641:b50::/44 to 64:ff9b::/96 lookup main prio 1000
ip -6 rule add from 64:ff9b::/96 to 2a0c:b641:b50::/44 lookup 200 prio 1001
ip -6 route add 64:ff9b::/96 via 2001:41d0:303:48a::1 dev enX4 table 200
```

This is a different shape than the v4 DNAT leak — Jool needs the
forward packet in default VRF (so its netfilter hook fires) and the
synthesised reply needs to escape back to overlay. See [CLAUDE.md
NAT64 section](../CLAUDE.md) for the full picture.

## Boot-order traps

These bit us during the post-reboot recovery on 2026-05-04. All three
are fixed in-tree now; the notes are here so the next surprise
diagnoses fast.

### `vrf` module load order vs `systemd-sysctl`

`net.ipv4.{tcp,udp}_l3mdev_accept` only exist as writable sysctls
**after** the `vrf` kernel module is loaded. Without them set to 1,
sockets bound to the wildcard address in the default VRF won't accept
connections arriving on overlay-VRF interfaces — Prometheus scrapes of
`node_exporter` from mon fail, sshd on the overlay loopback refuses
connections, etc.

The trap: `systemd-sysctl.service` runs early in boot (before
`network.target`). At that point networkd hasn't created the overlay
VRF yet, the kernel hasn't auto-loaded the `vrf` module, and the
sysctls don't exist. systemd-sysctl silently skips them, they default
to 0, and they stay 0 even after networkd later loads the module.

Fix: [`/etc/modules-load.d/vrf.conf`](../configs/rtr/modules-load.d/vrf.conf)
forces the module load via `systemd-modules-load.service`, which is
ordered before `systemd-sysctl.service`. Verify with
`sysctl net.ipv4.tcp_l3mdev_accept` (should be `1`).

### `jool-dkms` vs kernel upgrade

`apt install jool-dkms jool-tools` doesn't pull `linux-headers-amd64`
as a hard dependency — only the headers for the running kernel at
install time. After a kernel upgrade, DKMS has no source tree to build
against, the new module never gets compiled, and the next reboot
brings up a kernel with no `jool` module. Symptom:

```
modprobe[XXX]: modprobe: FATAL: Module jool not found in directory
/lib/modules/<new kernel>
jool.service: Failed with result 'exit-code'
```

NAT64 stays down until DKMS rebuilds. Fix: install the
`linux-headers-amd64` meta-package alongside `linux-image-amd64` so
the headers track every kernel upgrade and DKMS rebuilds Jool
automatically. Verify with
`dpkg -l linux-headers-amd64 | grep ^ii`.

### `systemd-networkd` reload under BGP load

Once FRR has the IPv6 DFZ loaded (~515k routes), any `networkctl
reload` or `systemctl restart systemd-networkd` enters a restart loop
with `Could not enumerate routes: Connection timed out` in the
journal. The kernel netlink dump exceeds networkd's 25s enumerate
timeout under that route count.

Live state in the kernel is unaffected — addresses, rules, and routes
already in place keep working. The risk is operational: you can't push
a networkd config change at runtime. Two paths:

1. **Push to disk and reboot.** At boot networkd runs before FRR
   populates BGP, enumerate is fast, all rules install cleanly. This
   is what we do today — see the "deploy networkd config" step in
   [deployment.md](deployment.md).
2. **Switch off networkd.** `ifupdown2` (Cumulus's package) does
   diff-based reloads with no enumerate, designed for BGP routers.
   Or move rtr to OpenBSD entirely. Tracked in
   [project_rtr_networkd_bgp memory].

If you find networkd in a restart loop after a runtime push, break it
with `systemctl stop systemd-networkd.service systemd-networkd.socket`
— the kernel keeps its current state — and plan a reboot.

## Verification cheatsheet

```bash
# Symmetric DNAT VRF rules in place
ip -4 rule show | grep -E '10\.0\.2\.0/24'
# Expected:
# 998: from all to 10.0.2.0/24 lookup 200 proto static
# 999: from 10.0.2.0/24 lookup main proto static

# No duplicate v4 route in main
ip -4 route show table main 10.0.2.0/24
# Expected: (empty)

# Overlay table holds the address-derived route
ip -4 route show table 200 10.0.2.0/24
# Expected: 10.0.2.0/24 dev enX2 proto kernel scope link src 10.0.2.1

# Boot-time sysctls applied
sysctl net.ipv4.tcp_l3mdev_accept net.ipv4.udp_l3mdev_accept
# Expected: both = 1

# vrf module loaded
lsmod | grep ^vrf

# Jool module built for current kernel
ls /lib/modules/$(uname -r)/updates/dkms/jool*.ko*
systemctl is-active jool

# UDP DNAT reliability (run from off-net)
ok=0; for i in $(seq 1 50); do dig +tries=1 +time=2 \
  @46.105.40.223 servify.network SOA +short >/dev/null 2>&1 && ok=$((ok+1)); done
echo "$ok/50"   # 50/50 = healthy
```
