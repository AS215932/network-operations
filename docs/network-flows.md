# AS215932 — Network flows

This doc is the canonical inventory of every traffic flow between hosts in
AS215932. It's the input dataset for the firewall role: every rule in
`ansible/inventory/host_vars/*.yml` traces back to a row here.

When you add a new service or peer, **update this file first**, then derive
the firewall rules.

Last sync: 2026-04-29 (verified live with `nft list ruleset` / `pfctl -sr`).

---

## Hosts

| Host | OS | IPv6 (overlay) | IPv4 | Role |
|------|-----|----------------|------|------|
| rtr | Debian 13 | `2a0c:b641:b50:2::1` | `46.105.40.223` (failover, NAT64) | router/NAT64/DNS forwarder/DNAT gateway |
| dns | Debian 13 | `2a0c:b641:b50:2::10` | (DNAT'd from `46.105.40.223`) | Knot authoritative DNS |
| api | Debian 13 | `2a0c:b641:b50:2::20` | — | hyrule-cloud (FastAPI :8402) + Postgres |
| web | Debian 13 | `2a0c:b641:b50:2::30` | — | nginx + uvicorn (:8080, :8081) |
| proxy | Debian 13 | `2a0c:b641:b50:2::40` | (DNAT'd from `46.105.40.223`) | Caddy TLS reverse proxy |
| mon | Debian 13 | `2a0c:b641:b50:2::50` | — | Prometheus + Grafana + Icinga2 + blackbox |
| vpn | Debian 13 | `2a0c:b641:b50:2::60` | (DNAT'd from `46.105.40.223`) | WireGuard server |
| xoa | Debian 13 | `2a0c:b641:b50:2::70`, `10.0.0.10` | — | Xen Orchestra |
| cr1-nl1 | FreeBSD 14.3 | loopback `2a0c:b641:b50::a` | — | core router (Servperso NL transit) |
| cr1-de1 | FreeBSD 15.0 | loopback `2a0c:b641:b50::b` | — | core router (Servperso DE + Extra-Transit + IXPs) |

dom0 is an XCP-NG hypervisor on the underlay only, not in this map.

---

## Subnets

| CIDR | Purpose |
|------|---------|
| `2a0c:b641:b50::/44` | AS215932 prefix (announced) |
| `2a0c:b641:b50::/64` | Router loopbacks (`::a` cr1-nl1, `::b` cr1-de1, `::d` rtr) |
| `2a0c:b641:b50:2::/64` | Infra subnet (rtr `::1`, VMs `::10`–`::70`) |
| `2a0c:b641:b50:3::/64` | VPN clients (routed via vpn VM) |
| `2a0c:b641:b50:ffXX::/127` | WireGuard tunnel /127s (mesh links) |
| `2a0c:b641:b51::/48` | Customer VM allocations |
| `10.0.0.0/24` | Mgmt v4 (dom0 ↔ XOA XAPI) |
| `10.0.2.0/24` | Legacy v4 DNAT targets on rtr |
| `2a02:a442:1016::/48`, `77.166.211.126/32` | Ops-prefix (KPN home, used for SSH and AXFR allow) |

---

## Per-host inbound flows

### rtr (`2a0c:b641:b50:2::1`)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| infra subnet, customer subnet, vpn-clients | TCP/UDP | 53 | DNS recursion (Unbound + DNS64) |
| mon | TCP | 9100 | node_exporter scrape |
| mon | TCP | 9342 | frr_exporter scrape |
| cr1-nl1, cr1-de1 loopbacks | TCP | 179 | iBGP |
| WireGuard mesh on `wg0`, `wg1` | OSPF6 (89) | — | OSPF6 between routers |
| cr1-nl1 underlay (`2a0c:b640:8:69::1`) | UDP | 1337 | WireGuard tunnel to NL |
| cr1-de1 underlay (`2a0c:b640:10::213`) | UDP | 1338 | WireGuard tunnel to DE |
| ops-prefix, vpn-clients | TCP | 22 | SSH |
| public internet (v4 DNAT'd from `46.105.40.223`) | TCP/UDP | 53 | → dns |
| public internet (v4 DNAT'd) | TCP | 80, 443 | → proxy |
| public internet (v4 DNAT'd) | UDP | 51820 | → vpn |

### dns (`2a0c:b641:b50:2::10`)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| any | TCP/UDP | 53 | Authoritative DNS queries |
| Openprovider secondaries (v6: `2a00:f10:121:400:4be:60ff:fe00:526`, `2a05:d014:f80:6e00:c937:174c:45eb:a5f7`) | TCP | 53 | AXFR |
| Openprovider secondaries (v4: `35.157.8.190`, `18.203.73.190`, `185.27.175.218`) | TCP | 53 | AXFR (DNAT'd) |
| ops-prefix | TCP | 53 | AXFR (per `configs/knot.conf.j2:47`) |
| api, proxy | TCP | 53 | RFC 2136 dyn updates (TSIG `hyrule-dns`) |
| mon | TCP | 9100 | node_exporter |
| ops-prefix, vpn-clients | TCP | 22 | SSH |

### api (`2a0c:b641:b50:2::20`)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| proxy | TCP | 8402 | hyrule-cloud API upstream |
| mon | TCP | 9100 | node_exporter |
| mon | TCP | 9187 | postgres_exporter |
| ops-prefix, vpn-clients | TCP | 22 | SSH |

### web (`2a0c:b641:b50:2::30`)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| proxy | TCP | 8080 | servify.network landing page |
| proxy | TCP | 8081 | as215932.net info site |
| mon | TCP | 9100 | node_exporter |
| ops-prefix, vpn-clients | TCP | 22 | SSH |

### proxy (`2a0c:b641:b50:2::40`)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| any | TCP | 80, 443 | Caddy TLS + ACME HTTP-01 |
| mon | TCP | 9100 | node_exporter |
| ops-prefix, vpn-clients | TCP | 22 | SSH |

### mon (`2a0c:b641:b50:2::50`)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| proxy | TCP | 3000 | Grafana via Caddy |
| self | TCP | 9100 | node_exporter (Prometheus self-scrape) |
| localhost only | TCP | 5665, 9090, 9115 | Icinga2 API / Prometheus / blackbox |
| ops-prefix, vpn-clients | TCP | 22 | SSH |

### vpn (`2a0c:b641:b50:2::60`)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| any | UDP | 51820 | WireGuard listener |
| mon | TCP | 9100 | node_exporter |
| ops-prefix, vpn-clients | TCP | 22 | SSH |

### xoa (`2a0c:b641:b50:2::70` overlay, `10.0.0.10` mgmt)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| proxy | TCP | 443 | XO web UI |
| dom0 (mgmt v4 `10.0.0.0/24`) | TCP | 80, 443 | XAPI back-channel |
| mon | TCP | 9100 | node_exporter |
| ops-prefix, vpn-clients | TCP | 22 | SSH |

### cr1-nl1 (`2a0c:b641:b50::a` loopback, `2a0c:b640:8:69::1` underlay)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| `2a0c:b640:10::ffff` (Servperso NL) | TCP | 179 | External BGP |
| any | UDP | 1337, 1338, 1340 | WireGuard underlay (cr1-de1, leg b, rtr) |
| ops-prefix | TCP | 22 | SSH |
| `wg*` mesh interfaces | any | — | All traffic — `pass quick on wg all no state` |
| any | ICMP, ICMPv6 | — | ping |
| any (transit) | inet6 | — | `from any to 2a0c:b641:b50::/44` (no state) |
| mon | TCP | 9100, 9342 | node_exporter, frr_exporter (newly explicit, was implicit via wg-pass) |

### cr1-de1 (`2a0c:b641:b50::b` loopback, `2a0c:b640:10::213` underlay)

| From | Proto | Port | Purpose |
|------|-------|------|---------|
| `2a0c:b640:10::ffff` (Servperso DE), `2a0c:b641:870::ffff` (Extra-Transit) | TCP | 179 | External BGP |
| any | UDP | 1337, 1338 | WireGuard underlay (cr1-nl1, rtr) |
| ops-prefix | TCP | 22 | SSH |
| `wg*` mesh interfaces | any | — | `pass quick on wg all no state` |
| any | ICMP, ICMPv6 | — | ping |
| any (transit) | inet6 | — | `from any to <AS215932> ` (no state) |
| mon | TCP | 9100, 9342 | node_exporter, frr_exporter (newly explicit) |

cr1-de1 listens on four interfaces (`vtnet0..3`); the WG/BGP/transit rules
are bound to `$ext_ifs` (the set), SSH/ICMP only on `$ext_if`.

---

## Per-host outbound flows (selected)

Most outbound is the mirror of someone else's inbound; the noteworthy
exceptions are:

| Host | Flow | Notes |
|------|------|-------|
| All Linux | → `deb.debian.org`, security mirrors | apt/unattended-upgrades |
| All Linux | → `pool.ntp.org` | NTP |
| All hosts | → rtr's Unbound (`::1` port 53) | Recursion (incl. NAT64 DNS64 synthesis) |
| api | → `api.openprovider.eu` HTTPS | Domain registration |
| api, proxy | → dns TCP 53 | RFC 2136 dyn updates (TSIG-authed) |
| proxy | → ACME providers HTTPS, dns TCP 53 | DNS-01 challenges |
| rtr | NAT64 outbound (Jool) → IPv4 internet | source = `46.105.40.223` |
| rtr, cr1-* | UDP 1337/1338/1340 → peer underlay | WireGuard mesh |
| mon | → all hosts:9100 + per-host scrape ports | Prometheus |
| mon | → public HTTPS, ICMP, DNS targets | blackbox checks |
| dns | → Openprovider secondaries (NOTIFY) | NOTIFY currently refused — see memory |

---

## Cross-cutting flows

| Flow | Direction | Port | Notes |
|------|-----------|------|-------|
| All hosts → rtr | out | 53 tcp/udp | DNS recursion via Unbound |
| mon → all hosts | out | 9100 tcp | node_exporter scrape |
| mon → api | out | 9187 tcp | postgres_exporter |
| mon → routers | out | 9342 tcp | frr_exporter |
| api/proxy → dns | out | 53 tcp | RFC 2136 dyn updates (TSIG) |
| dns ↔ Openprovider | both | 53 tcp | AXFR (NOTIFY broken — see memory) |
| rtr ↔ cr1-nl1, cr1-de1 underlay | both | 1337/1338 udp | WireGuard mesh |
| Public → rtr | in | 53/80/443/51820 | DNAT to dns/proxy/vpn |
| ops-prefix, vpn-clients → all | in | 22 tcp | SSH |

---

## Open question — Prometheus scrape on FreeBSD routers

The live pf rulesets on cr1-nl1 / cr1-de1 do **not** explicitly allow
Prometheus scrapes from mon. They work today because mon scrapes the
routers via the WireGuard mesh, where `pass quick on wg all no state`
allows everything. The Ansible-rendered configs add explicit
`9100/9342 from mon` rules for clarity, even though they're redundant
with the wg-pass. If we ever lock down the wg-pass (likely, eventually),
the explicit rules will already be in place.

---

## How to add a flow

1. Add a row to the relevant per-host inbound table above.
2. Add the matching `firewall_extra_rules` entry in
   `ansible/inventory/host_vars/<host>.yml`. Reference peers by name
   (`{{ peers.mon.ipv6 }}`), never by literal address.
3. Re-render: `cd ansible && ansible-playbook playbooks/firewall.yml --tags validate --connection=local`.
4. Review the diff under `ansible/generated/<host>/`.
5. Commit. Apply happens in a follow-up PR with `--tags apply` once review passes.
