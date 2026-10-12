# rtr VRF routing

`rtr` (Debian 13, OVH FR) splits its interfaces between two routing
contexts:

| VRF       | Table | Interfaces                         | Purpose                          |
|-----------|-------|------------------------------------|----------------------------------|
| _default_ | main  | `enX0` (mgmt), `enX4` (wan)        | Underlay v6 + failover v4        |
| `overlay` | 200   | `enX2` (infra), `enX3` (vm), `wg0`, `wg1`, `wg2`, `lo-overlay` | AS215932 v6 + iBGP |

FRR runs `bgpd` inside the overlay VRF (`router bgp 215932 vrf overlay`,
three iBGP peers to cr1.nl1 / cr1.de1 / cr1.ch1). Steady-state the kernel carries
~515k IPv6 routes — see [project_rtr_networkd_bgp memory] for why this
matters when restarting networkd.

VRF assignment is declarative in
[`configs/rtr/networkd/`](../configs/rtr/networkd/) — interfaces are in
the right VRF from boot, no runtime migration. The overlay netdev is
`05-overlay.netdev` (kind=vrf, table=200); each member interface
declares `VRF=overlay` in its `[Network]` block.

## Firewall matching in the overlay VRF

Customer VM isolation is enforced in rtr's nftables forward path. Do not rely
only on `iifname enX3 oifname enX2`: both `enX2` (infra) and `enX3` (customer)
are enslaved to the same `overlay` VRF, and netfilter/device exposure can differ
between VRF master and slave devices. The managed rules therefore include
VRF-safe destination-prefix drops. The same caveat applies to host-input
routing protocols: OSPFv3 packets may appear with `IN=overlay`, so rtr's input
filter allows proto 89 on both the VRF master (`overlay`) and WG slaves
(`wg0`/`wg1`/`wg2`).

- IPv6 forwarded from `2a0c:b641:b51::/48` to infra/router ranges
  (`2a0c:b641:b50:2::/64`, `2a0c:b641:b50::/64`, `2a0c:b641:b50:ff00::/56`)
  is dropped before any broad forward accepts.
- IPv4 forwarded from the customer bridge (`enX3`) to mgmt/legacy infra
  (`10.0.0.0/24`, `10.0.2.0/24`) is dropped before the public DNAT allows.

`ci-pr` (`2a0c:b641:b51::c1`) is the acceptance canary: `ci-pr → mon:9100`
must time out, while DNS to rtr on the customer gateway and public HTTPS egress
must still work.

## Customer VM addressing

`enX3` carries the customer aggregate `2a0c:b641:b51::/48`; FRR assigns
`2a0c:b641:b51::1/48` to the interface and advertises the aggregate. Paid VMs
use one `/64` each from that `/48`.

Do not enable `IPv6SendRA`, radvd, dnsmasq, Kea, or another link-wide
RA/DHCPv6 service on `enX3` for this design. The customer VMs share one L2
segment, and RA would advertise the same prefix information to every VM.
Instead, `hyrule-cloud` allocates a per-VM `/64` and passes static Debian
netplan `networkConfig` to XO. The guest address is `::2` inside its assigned
`/64`; the default route uses `2a0c:b641:b51::1` with `on-link: true`.

`2a0c:b641:b51::/64` remains reserved for router/legacy/static use, including
the existing `ci-pr` address. Proper IPAM is tracked separately in
<https://github.com/AS215932/network-operations/issues/346>; until then the
Hyrule allocator is the tactical source of VM prefix assignments.

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
ip -6 rule add to 2a0c:b641:b51::/48 lookup 200 prio 900
ip -6 rule add to 2a0c:b641:b50:2::/64 lookup 200 prio 901
ip -6 rule add from 2a0c:b641:b50::/44 to 64:ff9b::/96 lookup main prio 1000
ip -6 rule add from 64:ff9b::/96 to 2a0c:b641:b50::/44 lookup 200 prio 1001
ip -6 route add 64:ff9b::/96 via 2001:41d0:303:48a::1 dev enX4 table 200
```

This is a different shape than the v4 DNAT leak — Jool needs the
forward packet in default VRF (so its netfilter hook fires) and the
synthesised reply needs to escape back to overlay. The explicit destination
rules for `2a0c:b641:b51::/48` and `2a0c:b641:b50:2::/64` keep translated
replies for infrastructure and customer VMs from being routed out the underlay.

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
