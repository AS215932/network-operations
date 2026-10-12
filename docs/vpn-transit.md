# vpn VM on a dedicated transit segment

## Why

Until this change the vpn VM (WireGuard server for `2a0c:b641:b50:3::/64`)
sat on the shared infra /64 at `2a0c:b641:b50:2::60`, next to every other
infra host. Traffic from VPN clients to infra therefore took an asymmetric
path:

1. client -> vpn `wg0` -> vpn delivers **on-link** to e.g. mail `2::90`
   (rtr never sees the SYN);
2. mail has no route for `3::/64`, so the SYN-ACK goes to its default
   gateway rtr (`2::1`, `enX2`);
3. rtr has no conntrack entry for the flow, classifies the SYN-ACK as
   `ct state invalid` and drops it in the forward chain.

Verified on 2026-10-12: a capture on rtr `enX2` showed every SYN-ACK from
`2::90` arriving and none forwarded. ICMP from mail worked only because mail
originated it. Static routes on every infra host (or NAT on vpn) would hide
the problem. This change removes it at the topology level instead.

## Target design

| | rtr | vpn |
|---|---|---|
| Xen network | `xenbr-transit` (already exists, created by `ansible/roles/dom0`) | `xenbr-transit` |
| Interface | `enX1` (existing vif, currently DOWN/unconfigured), overlay VRF | `enX1` (new vif, device 1) |
| IPv6 | `2a0c:b641:b50:1::1/64` | `2a0c:b641:b50:1::60/64`, default via `1::1` |
| IPv4 | `10.0.1.1/24` | `10.0.1.60/24`, default via `10.0.1.1` (WireGuard DNAT only) |
| Infra /64 leg | unchanged (`enX2`) | **removed** (`enX0` / `2::60` unplugged) |

* `2a0c:b641:b50:1::/64` was reserved for "fw <-> rtr" transit on
  `xenbr-transit`. That firewall VM was never deployed, so the prefix and the
  bridge are reused here.
* rtr: `ipv6 route 2a0c:b641:b50:3::/64 2a0c:b641:b50:1::60` in VRF overlay
  (FRR static, still originated into iBGP via the existing `network` stmt).
* Policy rules on rtr (`nat64-vrf-leak.service`): prio 902
  (`to 3::/64 lookup 200`, added live on 2026-10-12) is persisted, and prio 903
  (`to 1::/64 lookup 200`) is added. Both mirror 900/901, so default-VRF
  lookups (Jool, rtr's own processes) for overlay prefixes behind vpn never fall
  through to main -> `enX4`.
* rtr nftables: v4 DNAT target `10.0.1.60`, DNS recursion allowed from
  `1::/64`, customer isolation extended to `1::/64`, `10.0.1.0/24` and
  `enX1`.
* vpn nftables forward: `wg0 <-> enX1` instead of `wg0 <-> enX0`.
* Inventory/DNS/monitoring: `peers.vpn.ipv6`, `hosts.yml`, `vpn` AAAA,
  PTRs, Prometheus, Icinga, and the log VM Vector allowlist all follow
  `2a0c:b641:b50:1::60`.

## Rollout (maintenance window, ~30 min, VPN clients down for a few minutes)

Pre-checks:
1. On dom0: `xe vif-list vm-name-label=rtr params=device,network-name-label`.
   Confirm rtr device 1 (`enX1`) is on `xenbr-transit`. If it is not, create
   or plug a vif on `xenbr-transit` (`xe vif-create ... device=1` +
   `xe vif-plug`, hot-plug, no reboot).
2. Have a non-VPN path to rtr and vpn: ops workstation v6 (`2a02:a442:1016::/48`
   is in the SSH allow set) or the Xen console. **Do not do this over the VPN.**
3. Save the live state: `ip -6 rule`, `ip -6 route show table 200`, and
   `nft list ruleset` on rtr, and `ip -br addr` on vpn.

Steps:
1. rtr: deploy `configs/rtr/networkd/10-enX1.network`, `networkctl reload &&
   networkctl reconfigure enX1`. Check `ip -br addr show enX1` and `ip link
   show enX1 | grep master overlay`.
2. rtr: add `interface enX1 vrf overlay` to FRR (`vtysh` or deploy
   `frr.conf` + `systemctl reload frr`). The 3::/64 static still points at
   `2::60` at this point.
3. dom0: add a vif for vpn on `xenbr-transit` (`xe vif-create
   vm-uuid=<vpn> network-uuid=<xenbr-transit> device=1` + `xe vif-plug`).
4. vpn: deploy `configs/vpn/10-enX1.network`, reconfigure. `ping -6
   2a0c:b641:b50:1::1` must work.
5. vpn: apply firewall (`ansible-playbook playbooks/firewall.yml -l vpn -e
   firewall_apply=true`; without `firewall_apply=true` the role only renders
   to `generated/` and changes nothing). The forward rules move to `enX1`.
   The inventory already targets `2a0c:b641:b50:1::60`, so accept its host key
   first (verify the fingerprint against the key seen on the old address).
   Note: while the `2::60` leg still exists, vpn reaches `2::/64` on-link via
   `enX0`, which the new forward rules no longer permit, so new client->infra
   flows stall until step 7. Keep steps 5-7 back-to-back.
6. rtr: switch the static (`no ipv6 route 3::/64 2::60` / `ipv6 route 3::/64
   1::60` in VRF overlay), apply the rtr firewall (DNAT -> `10.0.1.60`) and
   `systemctl restart nat64-vrf-leak` (adds 903; 902 is already live).
7. vpn: remove the `2::60` leg. Delete `/etc/systemd/network/10-enX0.network`,
   `networkctl reload`, then `xe vif-unplug` + `xe vif-destroy` for the
   `xenbr-infra` vif on dom0.
8. Update DNS zones (serial bumped), Prometheus, Icinga, and log firewall
   (`playbooks/firewall.yml -l log`, monitoring playbooks).

## Verification

* From a VPN client: `ssh kim@2a0c:b641:b50:2::90` (mail) completes. Also
  test one Linux infra host and rtr.
* rtr: `ip -6 route get 2a0c:b641:b50:3::4b from 2a0c:b641:b50:2::90 iif enX2`
  -> `via 2a0c:b641:b50:1::60 dev enX1 table 200`.
* rtr: `tcpdump -ni enX2 host 2a0c:b641:b50:3::4b` shows both SYN and SYN-ACK.
  The `ct state invalid` counter does not grow.
* v4 endpoint: a client on `46.105.40.223:51820` gets a fresh handshake.
* NAT64 from a client: `curl -6 http://ipv4.icanhazip.com` returns 46.105.40.223.
* mon: vpn host and node_exporter `[2a0c:b641:b50:1::60]:9100` are green.
  Vector logs from vpn arrive on log.

## Rollback

Any time before step 7 the old path still exists, so revert the FRR static
to `2::60` and re-apply the previous vpn/rtr firewall from `main`. After step
7: re-plug the `xenbr-infra` vif on vpn, restore `10-enX0.network`, revert the
static and DNAT target, and re-apply firewalls from the previous commit.
`enX1` on rtr can stay configured; it is inert without vpn on the segment.

## Impact / notes

* IPv6 WireGuard clients using `Endpoint = [2a0c:b641:b50:2::60]:51820` must
  switch to `[2a0c:b641:b50:1::60]:51820` (or `vpn.as215932.net`).
  IPv4 clients (`46.105.40.223:51820`) are unaffected.
