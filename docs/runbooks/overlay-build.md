# overlay VM — build runbook (Tor relay / i2pd / Yggdrasil)

Requested by Svag on 2026-10-18: a box in AS215932 that runs Tor, I2P and
Yggdrasil. This runbook takes the repo state from "prepared" to "live".

| Item | Value |
|------|-------|
| Host | `overlay` on xcp01, Debian 13, 2 vCPU / 2 GiB / 20 GB |
| IPv6 | `2a0c:b641:b50:2::71/64` (`overlay.as215932.net`, PTR) |
| IPv4 | `10.0.2.71/24`, private DNAT backend only |
| Access | dedicated `kim` operator user; no root / svag keys |
| Tor | **non-exit** relay `AS215932overlay`, ORPort `[::71]:9001`; IPv4 `46.105.40.223:901 -> 10.0.2.71:9001` via rtr DNAT |
| i2pd | transit router, NTCP2+SSU2 on `24316` tcp+udp, IPv6 only |
| Yggdrasil | outbound TLS peers only, `ygg0` in its own nft table (`table inet overlay_ygg`) |

## Why these ports

* `::71` — `::10`..`::f0` are taken and `::60` is the old vpn endpoint that
  IPv6 WireGuard clients may still use (docs/vpn-transit.md).
* Tor public IPv4 port `901` — rtr's `46.105.40.223` is shared with Jool:
  pool4 owns TCP/UDP `1024-60999` and rtr's own sockets use `61000-65535`. Any
  DNAT in those ranges collides with NAT64 sessions (the netproxy ports already
  do; see #546). `901` is below 1024, so it collides with neither.
* i2pd IPv6 only — IPv4 would ride rtr's masquerade on the same address Jool
  uses for UDP, which we are not touching before rtr-bsd.

## Decisions Svag owns before Tor goes live

1. **Relay vs exit.** The role only implements a non-exit relay
   (`ExitRelay 0`, `ExitPolicy reject *:*`, hard-coded). An exit would need an
   abuse contact, RIPE `abuse-c`, and OVH sign-off — and a role change.
2. **ContactInfo.** Replace `overlay_tor_contact_info` in
   `ansible/inventory/host_vars/overlay.yml`. `validate.yml` refuses the
   `CHANGEME` placeholder once `overlay_tor_enabled: true`.
3. **MyFamily** — only if we run more relays.

Until then `overlay_tor_enabled: false` (Tor stopped/disabled on the host) and
`rtr_overlay_dnat_enabled: false` (no rtr change).

## Build

1. Create the VM (dry run first, then `--apply`):

   ```sh
   # on xcp01 dom0, as kim (xe runs via sudo; xo-cli is not installed there)
   KIM_PUBKEY_FILE=~/id_kim.pub scripts/create-overlay-vm.sh
   KIM_PUBKEY_FILE=~/id_kim.pub scripts/create-overlay-vm.sh --apply
   # elsewhere, with xo-cli registered against XO:
   KIM_PUBKEY_FILE=~/.ssh/id_kim.pub BACKEND=xo scripts/create-overlay-vm.sh --apply
   ```

   The xe backend (`BACKEND=auto` picks it when `xe` is on PATH) does
   vm-install from the Debian 13 template, sets 2 GiB / 2 vCPU, grows the root
   VDI to 20 GiB, replaces the template VIFs with one infra VIF on a fresh
   random MAC that is checked against every VIF on the host, and attaches a
   NoCloud seed VDI (`overlay-cidata`, vfat label CIDATA). Detach and destroy
   the seed VDI after the first boot.

   cloud-init creates only `kim` (`/bin/sh`, NOPASSWD sudo), sets both
   addresses, disables root login. The template image ships a baked-in
   `svag` user + key; the cloud-config `runcmd` deletes it (and its
   `/etc/sudoers.d/90-cloud-init-users` rule) on first boot. `configs/overlay/10-enX0.network` is the
   reference networkd unit if the netplan render needs replacing.

2. From the workstation (`-u kim`), bootstrap the fleet accounts. Do this
   from an SSH source the firewall allows (ops / vpn / noc / mon / ci):
   after step 3 xcp01 dom0 is no longer an allowed source. Until the
   ci-runner-key step has run, CI cannot reach overlay — keep `:!overlay` in
   `scripts/ci/check-drift.sh` and Icinga registration (`monitoring_register:
   true`, applied by the monitoring playbook / post-merge drift apply) waits
   for it.

   ```sh
   KIM_KEY_PATH=<id_kim> ansible-playbook playbooks/kim-ops-key.yml --tags apply --limit overlay
   CI_KEY_PATH=<id_ci>   ansible-playbook playbooks/ci-runner-key.yml --tags apply --limit overlay
   ```

3. Firewall first (renders the `include "/etc/nftables.d/*.nft"` hook), then
   the overlay role, then monitoring/logs:

   ```sh
   ansible-playbook playbooks/firewall.yml   --tags apply -e firewall_apply=true --limit overlay
   ansible-playbook playbooks/overlay.yml    --tags apply -e overlay_apply=true
   ansible-playbook playbooks/monitoring.yml --tags apply -e monitoring_apply=true --limit overlay,mon
   ansible-playbook playbooks/logs.yml       --tags apply -e logs_apply=true --limit overlay,log
   ```

   The overlay role refuses to start Yggdrasil if `/etc/nftables.conf` lacks
   the include, loads `table inet overlay_ygg` before any daemon starts, and
   generates the Yggdrasil ed25519 key on the host (`/etc/yggdrasil/private.pem`,
   never in the repo). It prints `YGGDRASIL_ADDRESS overlay 2xx:...`.

4. Verify:

   ```sh
   nft list table inet overlay_ygg
   sysctl net.ipv6.conf.all.forwarding     # 0
   yggdrasilctl -endpoint unix:///run/yggdrasil/yggdrasil.sock getPeers
   curl -s http://127.0.0.1:7070/ | head  # i2pd console (ssh -L)
   ```

5. Rejoin the drift sweep: remove `:!overlay` from the default limit in
   `scripts/ci/check-drift.sh` (and its test).

## Releasing Tor (after Svag's decisions)

One PR that sets `overlay_tor_contact_info`, `overlay_tor_enabled: true` and
`rtr_overlay_dnat_enabled: true`, plus regenerated `ansible/generated/`.
Applying the rtr firewall reloads nftables **and restarts Jool** (firewall
handlers) — schedule it, and keep the 3-minute rollback watchdog. Check
`https://metrics.torproject.org/rs.html` for `AS215932overlay` after ~1 h.
