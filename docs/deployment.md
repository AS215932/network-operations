# Hyrule Infrastructure

Deployment configs and scripts for Hyrule Cloud on OVH dedicated servers with XCP-NG, AS215932.

**Everything internal is IPv6-only** using public AS215932 space. No RFC1918.

## AS215932 Addressing (2a0c:b641:b50::/44)

```
2a0c:b641:b50::/44  (full allocation)
├── 2a0c:b641:b50::/48   Infrastructure
│   ├── :0::/64   mgmt     dom0 ::1, fw ::2, xoa ::10
│   ├── :1::/64   transit   fw ::1, rtr ::2
│   └── :2::/64   infra     rtr ::1, dns ::10, api ::20, web ::30, proxy ::40
├── 2a0c:b641:b51::/48   Customer VMs (one /64 per VM via SLAAC)
└── 2a0c:b641:b52-b5f    Future expansion
```

OVH provides one public IPv4 per server — used only on the firewall's WAN interface for dual-stack external access. All internal traffic is pure IPv6.

## Architecture

```
Internet (IPv4 + IPv6)
  │
  ▼
[OVH Physical NIC] ── xenbr0 (WAN)
  │
  ▼
[fw - OpenBSD/pf] ── stateful filtering, IPv4→proxy redirect
  │
  │ xenbr-transit (2a0c:b641:b50:1::/64)
  │   fw ::1  ↔  rtr ::2
  ▼
[rtr - BIRD 2] ── BGP tunnels to transit, IPv6 routing
  │              │
  ▼              ▼
xenbr-infra    xenbr-vm
:2::/64        2a0c:b641:b51::/48
[proxy ::40]   [customer VMs]
[dns   ::10]
[api   ::20]
[web   ::30]

xenbr-mgmt (:0::/64): dom0 ::1, xoa ::10, fw ::2
```

## VM Layout (OVH RISE-S: 64GB RAM, 8c/16t)

| VM | Role | vCPU | RAM | Disk | OS | IPv6 |
|----|------|------|-----|------|----|------|
| xoa | Xen Orchestra | 2 | 4GB | 20GB | Debian 12 | 2a0c:b641:b50::10 |
| fw | Firewall (pf) | 2 | 1GB | 10GB | OpenBSD 7.6+ | ::2 / :1::1 |
| rtr | BGP Router | 2 | 2GB | 10GB | Debian 12 + BIRD 2 | :1::2 / :2::1 |
| proxy | TLS reverse proxy | 1 | 1GB | 10GB | Debian 12 + Caddy | :2::40 |
| dns | Authoritative DNS | 1 | 1GB | 10GB | Debian 12 + Knot | :2::10 |
| api | hyrule-cloud + PG | 2 | 4GB | 40GB | Debian 12 | :2::20 |
| web | hyrule-web | 1 | 2GB | 20GB | Debian 12 | :2::30 |

~15GB for infra, ~45GB available for customer VMs.

## File Layout

```
configs/
  pf.conf.j2                 OpenBSD firewall (IPv6 native + IPv4 WAN redirect)
  bird.conf.j2               BIRD 2 BGP (AS215932, 2a0c:b641:b50::/44)
  knot.conf.j2               Knot DNS + TSIG (IPv6-only listener)
  servify.network.zone.j2    Main DNS zone (dual-stack A+AAAA)
  deploy.servify.network.zone.j2  Dynamic zone (AAAA only)
  as215932.net.zone.j2       Forward zone for infra PTR names
  0.5.b...ip6.arpa.zone.j2  Reverse DNS (PTR) for 2a0c:b641:b50::/48
  Caddyfile.j2               TLS reverse proxy on proxy VM (IPv6 backends)
  hyrule-cloud.service       systemd unit for API
  hyrule-web.service         systemd unit for web frontend
  hyrule-cloud.env.j2        API environment (IPv6 addresses)
  hyrule-web.env.j2          Web environment
scripts/
  bootstrap-dom0.sh          Create XCP-NG bridges, set dom0 IPv6
  generate-tsig-key.sh       Generate TSIG key for DNS + API
  build-template.sh          Prepare IPv6-only customer VM template
  smoke-test.sh              End-to-end test (IPv6-first)
```

## Deployment Runbook

### Phase 0: Server Preparation

1. Order OVH RISE-S (ECO range, €65/mo)
2. Install XCP-NG 8.3 via OVH IPMI/KVM (NVMe RAID 1)
3. SSH into dom0 and run the bootstrap:
   ```bash
   scp scripts/bootstrap-dom0.sh root@<ovh-ip>:/tmp/
   ssh root@<ovh-ip> bash /tmp/bootstrap-dom0.sh
   ```
4. Reboot, save the network UUIDs printed by the script

### Phase 1: Xen Orchestra

5. Create a Debian 12 VM on `xenbr-mgmt` (2 vCPU, 4GB RAM, 20GB disk)
6. Assign static IPv6: `2a0c:b641:b50::10/64`
7. Install XO from sources:
   ```bash
   curl -sS https://raw.githubusercontent.com/ronivay/XenOrchestraInstallerUpdater/master/xo-install.sh | bash
   ```
8. Access XO at `https://[2a0c:b641:b50::10]` (via SSH tunnel or mgmt network)
9. Connect XO to dom0 (`2a0c:b641:b50::1`), generate API token

### Phase 2: Firewall (OpenBSD)

10. Create fw VM: 2 vCPU, 1GB RAM, 10GB disk
    - 3 NICs: xenbr0 (WAN), xenbr-mgmt, xenbr-transit
11. Configure interfaces:
    - `vio0` (WAN): OVH public IPv4 + route from AS215932 /44 via OVH
    - `vio1` (mgmt): `2a0c:b641:b50::2/64`
    - `vio2` (transit): `2a0c:b641:b50:1::1/64`
12. Deploy `configs/pf.conf.j2` → `/etc/pf.conf`
13. Enable forwarding:
    ```
    sysctl net.inet6.ip6.forwarding=1
    sysctl net.inet.ip.forwarding=1   # for IPv4 WAN only
    ```

**OVH networking:** OVH uses /32 IPv4 with point-to-point gateway:
```
# /etc/hostname.vio0
inet <pub-ip> 255.255.255.255
!route add -host <ovh-gw> -link -iface vio0
!route add default <ovh-gw>
```

### Phase 3: Router

14. Create rtr VM: 2 vCPU, 2GB RAM, 10GB disk
    - 3 NICs: xenbr-transit, xenbr-infra, xenbr-vm
15. Configure interfaces:
    - `eth0` (transit): `2a0c:b641:b50:1::2/64`
    - `eth1` (infra): `2a0c:b641:b50:2::1/64`
    - `eth2` (vm): `2a0c:b641:b51::1/48` (acts as gateway for customer /64s)
16. Default route: `ip -6 route add default via 2a0c:b641:b50:1::1`
17. Enable forwarding: `sysctl -w net.ipv6.conf.all.forwarding=1`
18. Install BIRD 2: `apt install bird2`
19. Deploy `configs/bird.conf.j2` → `/etc/bird/bird.conf`
20. Isolation (prevent customer VMs from reaching infra/mgmt):
    ```bash
    ip6tables -I FORWARD -i eth2 -o eth1 -j DROP
    ip6tables -I FORWARD -i eth2 -d 2a0c:b641:b50:0::/64 -j DROP
    ```

### Phase 3.5: TLS Reverse Proxy

21. Create proxy VM: 1 vCPU, 1GB RAM, 10GB disk (xenbr-infra, `2a0c:b641:b50:2::40`)
22. Install Caddy with rfc2136 DNS plugin:
    ```bash
    apt install golang
    go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
    ~/go/bin/xcaddy build --with github.com/caddy-dns/rfc2136
    mv caddy /usr/local/bin/
    ```
23. Deploy `configs/Caddyfile.j2` → `/etc/caddy/Caddyfile`
24. Enable and start Caddy: `systemctl enable --now caddy`

### Phase 4: DNS

22. Create dns VM: 1 vCPU, 1GB RAM, 10GB disk (xenbr-infra, `2a0c:b641:b50:2::10`)
23. Install Knot DNS: `apt install knot`
24. Generate TSIG key: `./scripts/generate-tsig-key.sh`
    - Key name **must** be `hyrule-dns` (hardcoded in `hyrule_cloud/providers/dns.py:36`)
25. Deploy `configs/knot.conf.j2` → `/etc/knot/knot.conf`
26. Deploy all zone files to `/var/lib/knot/zones/`:
    - `servify.network.zone` — main forward zone
    - `deploy.servify.network.zone` — dynamic VM subdomains
    - `as215932.net.zone` — infrastructure PTR names
    - `0.5.b.1.4.6.b.c.0.a.2.ip6.arpa.zone` — reverse DNS for 2a0c:b641:b50::/48
27. Start: `systemctl enable --now knot`
28. Update Openprovider:
    - NS records for servify.network → ns1/ns2.servify.network with glue A + AAAA
    - NS records for as215932.net → ns1/ns2.servify.network
    - Request RIPE rDNS delegation for 2a0c:b641:b50::/48 → ns1/ns2.servify.network
29. Test:
    ```bash
    dig @<pub-ip> servify.network AAAA
    dig @<pub-ip> proxy.as215932.net AAAA
    dig @<pub-ip> -x 2a0c:b641:b50:2::40   # should return proxy.as215932.net
    ```

### Phase 5: API Server

30. Create api VM: 2 vCPU, 4GB RAM, 40GB disk (xenbr-infra, `2a0c:b641:b50:2::20`)
31. Install PostgreSQL 17 (localhost-only):
    ```bash
    apt install postgresql-17
    sudo -u postgres createuser -s hyrule
    sudo -u postgres createdb -O hyrule hyrule
    sudo -u postgres psql -c "ALTER USER hyrule PASSWORD '<password>';"
    ```
32. Deploy hyrule-cloud to `/opt/hyrule-cloud`:
    ```bash
    useradd -r -s /usr/sbin/nologin hyrule
    python3.12 -m venv .venv && source .venv/bin/activate
    pip install .
    ```
33. Deploy `configs/hyrule-cloud.env.j2` → `/opt/hyrule-cloud/.env`
34. Run migrations: `.venv/bin/alembic upgrade head`
35. Deploy `configs/hyrule-cloud.service` → `/etc/systemd/system/`
36. Start: `systemctl enable --now hyrule-cloud`

### Phase 6: Web Frontend

37. Create web VM: 1 vCPU, 2GB RAM, 20GB disk (xenbr-infra, `2a0c:b641:b50:2::30`)
38. Deploy hyrule-web to `/opt/hyrule-web`:
    ```bash
    useradd -r -s /usr/sbin/nologin hyrule
    python3.12 -m venv .venv && source .venv/bin/activate
    pip install .
    ```
39. Deploy `configs/hyrule-web.env.j2` → `/opt/hyrule-web/.env`
40. Deploy `configs/hyrule-web.service` → `/etc/systemd/system/`
41. Start: `systemctl enable --now hyrule-web`

### Phase 7: Customer VM Template

42. Create minimal Debian 13 VM on xenbr-vm
43. Run: `scp scripts/build-template.sh root@[2a0c:b641:b51:...] && ssh ... bash build-template.sh`
44. Shut down, convert to template, add UUID to `XCPNG_TEMPLATES`

### Phase 8: BGP Peering

45. Establish GRE/WireGuard tunnels from rtr to transit provider(s)
46. Add BGP peer blocks to `bird.conf` (see template comments)
47. Reload BIRD: `birdc configure`
48. Verify: `birdc show protocols all`, check looking glass for 2a0c:b641:b50::/44

### Phase 9: Smoke Test

```bash
./scripts/smoke-test.sh servify.network <dev-bypass-secret>
```

## Key Notes

- **TSIG key name must be `hyrule-dns`** — hardcoded in `hyrule_cloud/providers/dns.py:36`
- **Caddy runs on dedicated proxy VM** (::40), not on the router — build with `xcaddy --with github.com/caddy-dns/rfc2136`
- **OVH uses /32 IPv4** — OpenBSD hostname.if needs host route to gateway
- **Customer VM isolation** — ip6tables on rtr blocks xenbr-vm → xenbr-infra/mgmt
- **Dev bypass** — set `PAYMENT_DEV_BYPASS_SECRET` for testing, clear for production
- **DNS zones** — servify.network has dual-stack A+AAAA; deploy.servify.network is AAAA-only
