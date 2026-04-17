# Hyrule Infrastructure Deployment

Deployment runbook for AS215932 (Hyrule/Servify) on OVH RISE-S with XCP-NG.

**Everything internal is IPv6-only** using public AS215932 space. No RFC1918.

## AS215932 Addressing (2a0c:b641:b50::/44)

```
2a0c:b641:b50::/44  (full allocation)
├── 2a0c:b641:b50::/48   Infrastructure
│   └── :2::/64   infra    rtr ::1, dns ::10, api ::20, web ::30, proxy ::40, mon ::50, vpn ::60, xoa ::70
├── 2a0c:b641:b51::/48   Customer VMs (one /64 per VM)
└── 2a0c:b641:b52-b5f    Future expansion
```

Mgmt bridge is **link-local only** — not part of the AS215932 address plan.
OVH provides two IPv4 addresses: dom0's primary (`193.70.32.138`) and a failover IP on rtr for NAT64.

## Architecture

```
Internet (IPv4 + IPv6)
  │
  ▼
[OVH Physical NIC] ── xenbr0 (WAN, dual-stack)
  │
  ▼
[dom0 / XCP-NG] ── underlay only, NDP proxy for rtr
  │
[rtr - Debian 13 + FRRouting]
  │  enX0 (mgmt, link-local)   enX4 (wan/underlay)     ← default VRF
  │  enX2 (infra)  enX3 (vm)  wg0  wg1  lo-overlay     ← overlay VRF
  │
  ├── xenbr-infra (:2::/64)
  │   [dns ::10]    Knot DNS (authoritative)
  │   [api ::20]    hyrule-cloud + Postgres 17
  │   [web ::30]    hyrule-web frontend
  │   [proxy ::40]  Caddy TLS reverse proxy
  │
  └── xenbr-vm (2a0c:b641:b51::/48)
      [customer VMs]

xenbr-mgmt (link-local): dom0, rtr enX0, xoa (+ 10.0.0.x for XOA→XAPI)
```

### Routers (WireGuard mesh, iBGP + OSPF6)

| Router | Location | OS | Underlay address | Loopback | Router-ID |
|--------|----------|-----|------------------|----------|-----------|
| cr1.nl1 | Servperso NL | FreeBSD + FRRouting | 2a0c:b640:8:69::1 | ::a | 1.1.1.1 |
| cr1.de1 | Servperso DE | FreeBSD + FRRouting | 2a0c:b640:10::213 | ::b | 2.2.2.2 |
| rtr | OVH FR | Debian 13 + FRRouting | 2001:41d0:303:48a::2 | ::d | 0.0.0.13 |

All loopbacks are in `2a0c:b641:b50::/128` (e.g. `2a0c:b641:b50::a`).

### WireGuard mesh

| Tunnel | Endpoints | Overlay /127 |
|--------|-----------|--------------|
| cr1.nl1 wg0 ↔ cr1.de1 wg0 | :1337 ↔ :1337 | ff00::/127 |
| cr1.nl1 wg3 ↔ rtr wg0 | :1340 ↔ :1337 | ff02::/127 |
| cr1.de1 wg1 ↔ rtr wg1 | :1338 ↔ :1338 | ff05::/127 |

WG endpoints are **underlay** addresses. WG link addresses are in `2a0c:b641:b50:ffXX::/127`.

## VM Layout (OVH RISE-S: 64GB RAM, 8c/16t)

| VM | Role | vCPU | RAM | Disk | OS | Network |
|----|------|------|-----|------|----|---------|
| xoa | Xen Orchestra | 2 | 4GB | 20GB | Debian 13 | mgmt (link-local + 10.0.0.10) + infra (::70) |
| rtr | Router + firewall | 2 | 2GB | 10GB | Debian 13 + FRRouting | mgmt + infra + vm + wan |
| dns | Authoritative DNS | 1 | 1GB | 10GB | Debian 13 + Knot | infra :2::10 |
| api | hyrule-cloud + Postgres 17 | 2 | 4GB | 40GB | Debian 13 | infra :2::20 |
| web | hyrule-web | 1 | 2GB | 20GB | Debian 13 | infra :2::30 |
| proxy | TLS reverse proxy | 1 | 1GB | 10GB | Debian 13 + Caddy | infra :2::40 |

~15GB for infra, ~45GB available for customer VMs.

## VM Provisioning (XO CloudConfig)

Infrastructure VMs are provisioned via Xen Orchestra's `vm.create` API with `cloudConfig` and `networkConfig` parameters. XO creates a ConfigDrive ISO that cloud-init reads on first boot.

**Template**: Debian 13 `generic` cloud image with cloud-init pre-installed, imported into XCP-NG as an HVM/UEFI template. The `generic` variant is required — `genericcloud` lacks Xen drivers and `nocloud` doesn't detect XO's ConfigDrive.

```bash
# Example: create a VM via xo-cli (see scripts/create-vms.sh)
VM_ID=$(xo-cli vm.create \
  name_label="dns" \
  template=<template-uuid> \
  VIFs='json:[{"network":"<infra-net-uuid>"}]' \
  CPUs.number=1 \
  memory=1073741824 \
  bootAfterCreate=false \
  destroyCloudConfigVdiAfterBoot=true \
  cloudConfig="<cloud-config yaml>" \
  networkConfig="<netplan v2 yaml>")

# Resize template disk before boot (cloud-init grows partition on first boot)
VDI_ID=$(xo-cli --list-objects type=VBD VM="$VM_ID" is_cd_drive=false | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['VDI'] if d else '')")
xo-cli vdi.set id="$VDI_ID" size=10737418240

# Set UEFI boot order: disk only (no PXE)
xo-cli vm.setBootOrder vm="$VM_ID" order=c
xo-cli vm.start id="$VM_ID"
```

NIC naming: Debian on Xen uses `enX0` (xe-guest-utilities). Use `enX0` in both cloud-init and networkd configs.

## Deployment Runbook

### Phase 0: Server Preparation

1. Order OVH RISE-S (ECO range, €65/mo)
2. Install XCP-NG 8.3 via OVH IPMI/KVM (NVMe RAID 1)
3. SSH into dom0 and run bootstrap:
   ```bash
   scp scripts/bootstrap-dom0.sh root@<ovh-ip>:/tmp/
   ssh root@<ovh-ip> bash /tmp/bootstrap-dom0.sh
   ```
   Creates bridges: xenbr-mgmt, xenbr-infra, xenbr-vm
4. Configure IPv6 forwarding and NDP proxy for rtr (see `configs/dom0/network-setup.sh`)

### Phase 1: Xen Orchestra

5. Create Debian 13 VM with two NICs: xenbr-mgmt (enX0) and xenbr-infra (enX1).
   2 vCPU, 4GB, 20GB.
6. Assign addresses:
   - enX0 (mgmt): static IPv4 `10.0.0.10/24`, link-local IPv6 auto, **no default route**.
   - enX1 (infra): static IPv6 `2a0c:b641:b50:2::70/64`, default route via `2a0c:b641:b50:2::1`.
   Deploy `configs/xoa/10-enX1.network` to `/etc/systemd/network/`.
7. Install XO from sources. XO binds to all interfaces by default, so the
   UI is reachable on both addresses.
8. Connect XO to dom0 via `10.0.0.1` (mgmt-side XAPI), generate API token
   for hyrule-cloud. Public UI is served at `https://xo.servify.network`
   via Caddy on the proxy VM.

### Phase 2: VM Template

9. Download Debian 13 `generic` cloud image on dom0
10. Install cloud-init via chroot into the image
11. Import into XCP-NG as HVM/UEFI template via `xe vdi-import`
12. See `scripts/build-template.sh` for the full procedure

### Phase 3: Router (rtr)

13. Create rtr VM: 2 vCPU, 2GB RAM, 10GB disk
    - 4 NICs: mgmt (enX0), infra (enX2), vm (enX3), wan (enX4)
14. Remove netplan, deploy systemd-networkd configs (`configs/rtr/networkd/`):
    - enX0 (mgmt): link-local only, default VRF
    - enX2 (infra): overlay VRF — address `2a0c:b641:b50:2::1/64` via FRR
    - enX3 (vm): overlay VRF — address `2a0c:b641:b51::1/48` via FRR
    - enX4 (wan): default VRF — `2001:41d0:303:48a::2/64` (OVH underlay)
    - WireGuard tunnels (wg0, wg1) and lo-overlay created as .netdev in overlay VRF
15. Deploy sysctl (`configs/rtr/sysctl.conf`): IPv6 forwarding, disable DAD on enX4
16. Install FRRouting, deploy `configs/rtr/frr.conf`
    - Overlay VRF with WireGuard tunnels
    - All overlay IPv6 addresses assigned by FRR
    - iBGP mesh with cr1.nl1 and cr1.de1
    - Announces /44 aggregate + infra /64 + vm /48 into iBGP
17. Copy WG private key to `/etc/wireguard/private.key` (mode 0640, root:systemd-network)
18. Customer VM isolation (nftables):
    ```bash
    nft add rule inet filter forward iifname "enX3" oifname "enX2" drop
    nft add rule inet filter forward iifname "enX3" ip6 daddr 2a0c:b641:b50::/64 drop
    ```

### Phase 3b: NAT64/DNS64 on rtr

The overlay network is IPv6-only. NAT64 + DNS64 provides IPv4 reachability for overlay clients (VMs, Unbound itself) to reach IPv4-only services on the internet.

**Prerequisites**: Order an OVH failover IPv4. Assign a virtual MAC in OVH panel and set it on rtr's enX4 VIF in XO.

19. Add failover IPv4 to rtr's enX4 (`configs/rtr/networkd/10-enX4.network`):
    ```ini
    [Address]
    Address=<failover-ipv4>/32
    [Route]
    Destination=0.0.0.0/0
    Gateway=193.70.32.254
    ```
20. Enable IPv4 forwarding: add `net.ipv4.conf.all.forwarding=1` to sysctl
21. Install Jool: `apt install jool-dkms jool-tools`
22. Deploy `configs/rtr/jool/jool.conf` → `/etc/jool/jool.conf`:
    - instance name: `nat64` (netfilter framework)
    - pool6: `64:ff9b::/96`
    - pool4: `<failover-ipv4>` entries for TCP/UDP (1024-65535) **and ICMP** (0-65535)
      — all three protocols are required; without the ICMP entry, ping-based
      reachability checks silently fail even though TCP/UDP work.
    - Enable the stock `jool.service` shipped by `jool-tools` (it runs
      `jool file handle /etc/jool/jool.conf` on start).
23. Deploy `configs/rtr/jool/nat64-vrf-leak.service` → `/etc/systemd/system/`
    and `systemctl enable --now nat64-vrf-leak`. It installs both VRF leak
    rules plus a table-200 route for the NAT64 prefix:
    ```
    # Forward: overlay clients → Jool in default VRF (so Jool's netfilter
    # hook sees the packet; without this rule overlay traffic never reaches
    # Jool and all NAT64 checks time out)
    ip -6 rule add from 2a0c:b641:b50::/44 to 64:ff9b::/96 lookup main prio 1000
    # Return: Jool reply (src 64:ff9b::...) → overlay VRF so it reaches the VM
    ip -6 rule add from 64:ff9b::/96 to 2a0c:b641:b50::/44 lookup 200 prio 1001
    # Route in overlay VRF giving clients a next-hop for the NAT64 prefix
    ip -6 route add 64:ff9b::/96 via 2001:41d0:303:48a::1 dev enX4 table 200
    ```
    The unit is ordered `Before=jool.service` so rules are in place before
    Jool starts.
24. Enable DNS64 + DNSSEC in Unbound:
    ```
    module-config: "dns64 validator iterator"
    dns64:
        prefix: 64:ff9b::/96
    ```
25. Verify from an overlay VM (e.g. mon):
    ```
    dig AAAA files.pythonhosted.org    # should return 64:ff9b::...
    ping6 64:ff9b::0101:0101           # 1.1.1.1 via NAT64; must reply
    ```
    The Icinga check `nat64-ipv4-reachability` on rtr exercises this same
    ping6 path end-to-end. A failure there means one of: Jool down, pool4
    missing a protocol, VRF leak rules missing, failover IPv4 unbound on
    enX4, or upstream IPv4 broken — debug with
    `jool -i nat64 stats display --all | awk '$2 != 0'` on rtr.

### Phase 4: DNS

19. Create dns VM via XO CloudConfig: 1 vCPU, 1GB, 10GB, infra network, `::10`
20. Install Knot DNS: `apt install knot`
21. Generate TSIG key: `./scripts/generate-tsig-key.sh`
    - Key name **must** be `hyrule-dns` (hardcoded in `hyrule_cloud/providers/dns.py:36`)
22. Deploy `configs/knot.conf.j2` → `/etc/knot/knot.conf`
23. Deploy zone files to `/var/lib/knot/zones/`
24. Update registrar NS records, request RIPE rDNS delegation

### Phase 5: API Server (hyrule-cloud)

25. Create api VM via XO CloudConfig: 2 vCPU, 4GB, 40GB, infra network, `::20`
26. Populate secrets in `secrets.local.sh` at the repo root (gitignored) —
    see the header of `scripts/bootstrap-app.sh` for the required variable list.
27. Run `./scripts/bootstrap-app.sh cloud`. The script installs Postgres 17,
    creates the `hyrule` role/db, generates a per-VM deploy key (prints it
    for you to paste into the GitHub repo's Deploy keys page), clones
    `AS215932/hyrule-cloud`, renders and installs `/opt/hyrule-cloud/.env`
    from `configs/hyrule-cloud.env.j2`, installs the systemd unit, and runs
    the first `uv sync`. It does not start the service — inspect logs first.
28. `ssh root@[::20] systemctl start hyrule-cloud` once verified.

### Phase 6: Web Frontend (hyrule-web)

29. Create web VM via XO CloudConfig: 1 vCPU, 2GB, 20GB, infra network, `::30`
30. Run `./scripts/bootstrap-app.sh web`. Same flow as cloud, minus Postgres.
31. `ssh root@[::30] systemctl start hyrule-web`.

### Iterating on code (live deploys after initial bootstrap)

Both repos live on GitHub under the `AS215932` org. The bootstrap step put
per-VM read-only deploy keys on each VM, so pulls work without any human
credential on the box. To ship a change:

```bash
# In ~/Dev/hyrule-web (or ~/Dev/hyrule-cloud):
git commit -am "..."
git push origin main

# Then from ~/Dev/hyrule-infra:
./scripts/deploy-app.sh web      # or: deploy-app.sh cloud
```

`deploy-app.sh` SSHes to the target VM, `git fetch`es the latest
`origin/main`, runs `uv sync --frozen`, runs migrations (cloud only), and
restarts the systemd service. Total time is a few seconds when deps haven't
changed.

Rolling back is the same pattern with a git ref:
`./scripts/deploy-app.sh web <sha>`.

### Phase 7: TLS Reverse Proxy (proxy)

35. Create proxy VM via XO CloudConfig: 1 vCPU, 1GB, 10GB, infra network, `::40`
36. Install Caddy (built with `xcaddy --with github.com/caddy-dns/rfc2136`)
37. Deploy Caddyfile:
    - `servify.network` → `http://[2a0c:b641:b50:2::30]:8080` (web)
    - `api.servify.network` → `http://[2a0c:b641:b50:2::20]:8402` (api)
    - DNS-01 ACME via RFC 2136 against Knot DNS (`::10`)
38. Deploy systemd unit, start service

### Phase 8: Customer VM Template

39. Create minimal Debian 13 VM on xenbr-vm
40. Install: cloud-init (ConfigDrive), xe-guest-utilities, openssh-server
41. Clean: `cloud-init clean --logs --seed`, remove machine-id
42. Convert to XCP-NG template, add UUID to api `.env` `XCPNG_TEMPLATES`

### Phase 9: BGP Peering

Already live via WireGuard mesh to cr1.nl1 and cr1.de1 (see configs/).

Transit peers:
- AS34872 (Servperso) on cr1.nl1 and cr1.de1
- AS210233 on cr1.de1

BGP policy: `TRANSIT-IN` (as-path filter), `TRANSIT-OUT` (prefix-list). iBGP peers use `next-hop-self` only.

### Phase 10: Smoke Test

```bash
./scripts/smoke-test.sh servify.network <dev-bypass-secret>
```

## Key Notes

- **TSIG key name must be `hyrule-dns`** — hardcoded in `hyrule_cloud/providers/dns.py:36`
- **Caddy runs on proxy VM (`::40`)** — NOT on rtr. Build with `xcaddy --with github.com/caddy-dns/rfc2136`
- **FRRouting, not BIRD** — all routers use FRR. FreeBSD core routers use `doas`, not `sudo`.
- **Overlay VRF on rtr** — enX2, enX3, WG, lo-overlay in overlay VRF; enX0 (link-local), enX4 (underlay) in default VRF
- **dom0 is underlay-only** — no AS215932 addresses on dom0. Mgmt bridge is link-local.
- **systemd-networkd on rtr** — replaces netplan. VRF assignment at boot, addresses via FRR.
- **Customer VM isolation** — nftables on rtr drops forwarding from xenbr-vm to xenbr-infra/mgmt
- **Static IPs only** — no DHCP for infrastructure VMs
- **NAT64/DNS64** — Jool (`64:ff9b::/96`) on rtr in default VRF, with policy routing rules leaking traffic between overlay VRF (table 200) and default VRF. DNS64 in Unbound synthesizes AAAA for IPv4-only domains. Failover IPv4 on enX4 (OVH virtual MAC) — not dom0's IP.
- **Dev bypass** — set `PAYMENT_DEV_BYPASS_SECRET` for testing, clear for production
