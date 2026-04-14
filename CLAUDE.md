# AS215932 Network Operations

Infrastructure-as-code for AS215932 (Hyrule/Servify), an IPv6-first ISP running on XCP-NG.

## Project overview

This repo contains live router configs, service templates, provisioning scripts, and deployment docs for the AS215932 network. The primary deployment target is an OVH RISE-S dedicated server running XCP-NG with multiple VMs.

## Network fundamentals

- **ASN**: 215932 (RIPE)
- **Prefix**: `2a0c:b641:b50::/44`
- **Internal networking is IPv6-only** — no RFC1918. All VMs use public AS215932 addresses.
- IPv4 exists only on dom0's WAN bridge (OVH-provided /32).
- Domain: `servify.network` (public services), `as215932.net` (infrastructure names), `deploy.servify.network` (dynamic VM records)

## Architecture

Underlay (hosting provider networks) is separate from overlay (AS215932 `2a0c:b641:b50::/44`). WireGuard tunnels connect routers over underlay; overlay traffic runs inside the tunnels. WireGuard endpoints MUST be underlay addresses, never overlay.

### Routers

| Router | Location | OS | Underlay address | Loopback (overlay) | Router-ID |
|--------|----------|-----|------------------|-------------------|-----------|
| cr1.nl1 | Servperso NL | FreeBSD + FRRouting | `2a0c:b640:8:69::1` | `::a` | 1.1.1.1 |
| cr1.de1 | Servperso DE | FreeBSD + FRRouting | `2a0c:b640:10::213` | `::b` | 2.2.2.2 |
| rtr | OVH FR | Debian 13 + FRRouting | `2001:41d0:303:48a::2` | `::d` | 0.0.0.13 |

All loopbacks are in `2a0c:b641:b50::/128` (e.g. `2a0c:b641:b50::a`).

### WireGuard mesh

| Tunnel | Endpoints | Overlay /127 |
|--------|-----------|--------------|
| cr1.nl1 wg0 ↔ cr1.de1 wg0 | :1337 ↔ :1337 | `ff00::/127` |
| cr1.nl1 wg3 ↔ rtr wg0 | :1340 ↔ :1337 | `ff02::/127` |
| cr1.de1 wg1 ↔ rtr wg1 | :1338 ↔ :1338 | `ff05::/127` |

WG link addresses are in `2a0c:b641:b50:ffXX::/127`. Global addresses on links for traceroute visibility.

### OVH VM layout

| VM | Role | OS | NICs |
|----|------|----|------|
| rtr | Router + firewall | Debian 13 | enX0(mgmt), enX2(infra), enX3(vm), enX4(wan) |
| dns | Authoritative DNS | Debian 13 | infra |
| api | hyrule-cloud + Postgres | Debian 13 | infra |
| web | hyrule-web | Debian 13 | infra |
| proxy | TLS reverse proxy (Caddy) | Debian 13 | infra |
| vpn | WireGuard VPN | Debian 13 | infra |
| xoa | Xen Orchestra | Debian 13 | mgmt |

dom0 is **underlay-only** — no AS215932 addresses. It acts as NDP proxy for rtr's OVH underlay address. mgmt bridge uses link-local IPv6 + `10.0.0.1/24` (for XOA→XAPI).

rtr uses an overlay VRF (table 200) via systemd-networkd (not netplan). Interface assignment:
- **Default VRF**: enX0 (mgmt, link-local only), enX4 (wan/underlay)
- **Overlay VRF**: enX2 (infra), enX3 (vm), wg0, wg1, lo-overlay

Interfaces are in the correct VRF from boot — no runtime VRF migration. SSH to rtr: dom0 → `2001:41d0:303:48a::2` (underlay, same L2 on xenbr0).

## Addressing

```
mgmt bridge           link-local only (dom0, rtr enX0, xoa)
2a0c:b641:b50:2::/64  infra     rtr ::1, dns ::10, api ::20, web ::30, proxy ::40, vpn ::60
2a0c:b641:b50:3::/64  vpn clients (routed via vpn VM)
2a0c:b641:b51::/48    customer VMs (one /64 each)
```

## Repository layout

- `configs/<router>/` — Live FRR and WireGuard configs per router (`rtr/`, `cr1-de1/`, `cr1-nl1/`, `dom0/`).
- `configs/rtr/networkd/` — systemd-networkd `.netdev` and `.network` files for rtr (replaces netplan + overlay-vrf.service).
- `configs/` — Jinja2 templates for services not yet deployed (Knot DNS, Caddy, systemd units, DNS zones, env files).
- `autoinstall/` — OS autoinstall response files (OpenBSD, Debian cloud-init) and QMP tools for headless VM interaction.
- `scripts/` — Shell scripts for dom0 bootstrap, TSIG key generation, VM template prep, and smoke tests.
- `docs/` — Deployment runbook and architecture docs.

## Key conventions

- Static IPs only — never DHCP for infrastructure VMs.
- All routers use **FRRouting** (not BIRD). Core routers (cr1.*) run FreeBSD; rtr runs Debian.
- On FreeBSD hosts: use `doas` (not sudo), `ifconfig` (not ip), `netstat -rn` (not ip route).
- NIC naming: Debian on Xen uses `enX0`/`enX1`/`enX2`/etc.
- Each WG peer needs a `/128` static route for the remote's underlay address, pinned to the physical gateway, to prevent overlay BGP routes from swallowing underlay traffic.
- Config files include a comment header with the target deploy path.

## BGP policy

- **Transit route-maps**: `TRANSIT-IN` (match as-path 1) and `TRANSIT-OUT` (match prefix-list AS215932v6-out) applied to all transit peers.
- **AS-path filter** (as-path access-list 1): denies own ASN (loop prevention), private 16-bit ASNs (64512-65535), private 32-bit ASNs (4200000000-4294967295), and paths longer than 200 chars.
- iBGP peers have no transit filters — only `next-hop-self` and `soft-reconfiguration inbound`.

## NAT64/DNS64

The overlay network is IPv6-only, but some external services (authoritative DNS servers, package repos) are IPv4-only. NAT64 + DNS64 on rtr provides IPv4 reachability for all overlay clients.

### Components

- **DNS64** (Unbound on rtr): When a domain has only A records (no AAAA), Unbound synthesizes a AAAA by embedding the IPv4 address in `64:ff9b::/96`. Example: `93.184.216.34` → `64:ff9b::5db8:d822`.
- **NAT64** (Jool on rtr): Kernel module that translates IPv6 packets destined to `64:ff9b::/96` into IPv4 packets, using an OVH failover IP as the source address (pool4).

### VRF route leaking

Jool does not support VRF — it runs in the default VRF only. Overlay VRF clients reach Jool via policy routing rules that leak the NAT64 prefix between VRFs:

```
# Forward: overlay → default VRF (so Jool sees the packet)
ip -6 rule add from 2a0c:b641:b50::/44 to 64:ff9b::/96 lookup main prio 1000

# Return: default → overlay VRF (so the translated reply reaches the VM)
ip -6 rule add from 64:ff9b::/96 to 2a0c:b641:b50::/44 lookup 200 prio 1001
```

The forward rule says: if source is AS215932 and destination is the NAT64 prefix, use the main (default VRF) routing table instead of overlay table 200. Jool's netfilter hook intercepts the packet, translates to IPv4, and sends it out enX4.

The return rule says: when Jool translates the IPv4 reply back to IPv6 (src=`64:ff9b::...`, dst=AS215932 address), use overlay table 200 to route the response back to the client VM.

### IPv4 addressing

rtr's enX4 (on xenbr0) carries both the OVH underlay IPv6 and a failover IPv4 for NAT64. The failover IP uses an OVH virtual MAC assigned to rtr's VIF — dom0's IPv4 (`193.70.32.138`) is not used for NAT64 traffic.

### Key files

- `configs/rtr/jool/jool.conf` — Jool instance config (pool6 + pool4). Instance name is `nat64` (not `default`); `jool -i nat64 ...` everywhere. Pool4 must include TCP, UDP, **and ICMP** — omitting any protocol breaks traffic of that type silently.
- Jool itself runs under the stock `jool.service` from `jool-tools`.
- `configs/rtr/jool/nat64-vrf-leak.service` — systemd oneshot that installs the forward rule, return rule, and overlay-VRF route (table 200) for `64:ff9b::/96`. Ordered `Before=jool.service`. If only the return rule is installed, overlay traffic never reaches Jool and all NAT64 checks time out.
- Unbound config on rtr — `module-config: "dns64 validator iterator"` + `dns64: prefix: 64:ff9b::/96`.

### Verifying

From an overlay VM: `ping6 64:ff9b::0101:0101` (embedded 1.1.1.1) should reply. The `nat64-ipv4-reachability` Icinga check on rtr runs exactly this ping from mon. If it fails, the fastest triage is `jool -i nat64 stats display --all | awk '$2 != 0'` on rtr — non-zero counters for `POOL4_MISMATCH`, `BIB4_NOT_FOUND`, or `POOL6_MISMATCH` each point at a distinct layer.

## Critical details

- **TSIG key name must be `hyrule-dns`** — hardcoded in `hyrule-cloud` API at `hyrule_cloud/providers/dns.py:36`.
- **Caddy** runs on a dedicated proxy VM (`::40`), NOT on rtr. Built with `xcaddy --with github.com/caddy-dns/rfc2136` for DNS-01 ACME challenges against Knot. Terminates TLS and reverse-proxies to web (`:8080`) and api (`:8402`).
- **Customer VM isolation**: nftables on rtr drops forwarding from xenbr-vm to xenbr-infra/xenbr-mgmt.

## Related repositories

- `hyrule-cloud` — API server (FastAPI + PostgreSQL) for VM lifecycle management
- `hyrule-web` — Web frontend (served by the web VM)
