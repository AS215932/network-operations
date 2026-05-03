# Ansible operations runbook (hyrule-infra)

The `ansible/` tree is the new home for declarative provisioning of AS215932.
First role to land: `firewall`. Future roles (knot, caddy, frr, exporters,
system_updates) drop into the same layout.

## Prerequisites

- Ansible core ≥ 2.14 (`pip install --user ansible` or distro package).
- `~/.ssh/id_servify` present (per [memory feedback_ssh_key.md](../../.claude/projects/-home-svag-Dev/memory/feedback_ssh_key.md)).
- Python 3 on every managed host:
  - Debian: `/usr/bin/python3` (preinstalled).
  - FreeBSD: `/usr/local/bin/python3` (`pkg install python3` if missing).
- `doas` configured on the FreeBSD routers for the `svag` user. One-time
  bootstrap: `doas pkg install py311-ansible-core` and `permit nopass keepenv :wheel` in `/usr/local/etc/doas.conf`.
- `at(1)` available on every host for the rollback watchdog (Linux: `apt install at && systemctl enable --now atd`; FreeBSD ships `atrun` in base).

## Running the validate-only pipeline (PR #1 default)

```
cd ansible
ansible-playbook playbooks/firewall.yml \
  --tags validate \
  --connection=local \
  --skip-tags=snapshot
```

This:
1. Walks every host in inventory (skipping dom0).
2. Renders `nftables.conf` or `pf.conf` per host into `ansible/generated/<host>/`.
3. Does not connect to any remote host (`--connection=local` short-circuits).
4. Does not touch any live config.

Review the rendered files with `git diff` after a render run.

## Running an apply (later, post-merge)

```
ansible-playbook playbooks/firewall.yml \
  --tags apply \
  --limit dns \
  -e firewall_apply=true
```

This:
1. SSHes to the host (no `--connection=local`).
2. Renders the config to `<conf_path>.new` on the host.
3. Runs `nft -c -f` / `pfctl -nf` to syntax-check.
4. Backs up the currently-loaded ruleset.
5. Schedules a 3-minute `at` watchdog that reverts to the backup if the play
   doesn't cancel it.
6. Atomically moves the new config into place and triggers the reload handler.
7. On rtr, the handler also restarts `nat64-vrf-leak` and `jool` (in that order). The IPv4 DNAT VRF leak lives in `10-enX2.network` as routing-policy rules and needs no per-reload restart.
8. Cancels the watchdog only if the play completes cleanly.

`serial: 1` is set on the firewall play, so a bad rule blocks the next host.

### Rollout order (recommended)

1. `--limit dns` — lowest blast radius, exercises the path.
2. `--limit api,web,proxy,mon,vpn,xoa` — remaining VMs.
3. `--limit cr1-nl1,cr1-de1` — FreeBSD routers (live ruleset preserved, only labels and 2 monitoring rules differ).
4. `--limit rtr` — highest blast radius.

After step 4 with `firewall_input_policy: accept` (logging only), watch counters
for 24h via `nft list ruleset` and Icinga. Then flip `firewall_input_policy: drop`
in `host_vars/rtr.yml` and re-apply.

## Inventory

- `inventory/hosts.yml` — static, IPv6 ansible_host per VM.
- `inventory/group_vars/all.yml` — the canonical `peers` dict + subnets + ops-prefix. **Edit this when a host moves.**
- `inventory/group_vars/{linux,freebsd}.yml` — OS-family defaults (user, become, python).
- `inventory/group_vars/{routers,infra_vms,public_facing}.yml` — role-based posture.
- `inventory/host_vars/<host>.yml` — host-specific `firewall_extra_rules`.

## Adding a new firewall rule

1. Edit [docs/network-flows.md](network-flows.md) — add a row to the relevant table. *This is the source of truth.*
2. Edit `inventory/host_vars/<host>.yml` and append to `firewall_extra_rules`. Reference peers by name (`{{ peers.mon.ipv6 }}`), never literal addresses.
3. Re-render: `ansible-playbook playbooks/firewall.yml --tags validate --connection=local --skip-tags=snapshot`.
4. Inspect `ansible/generated/<host>/` diff in your PR.

Rule shape:

```yaml
- proto: tcp           # tcp | udp | tcp+udp | icmp | icmp6 | ospf
  dport: 9100          # int | "80-100" | [80, 443]
  src: any             # any | "addr/cidr" | [list of addrs]
  family: ip6          # ip6 (default) | ip | both
  iifname: wg0         # optional, restrict to interface
  comment: "required, becomes the rule comment"
```

For pf-only constructs that don't fit the data model (e.g. `match all scrub`,
new transit pass rules), use `firewall_extra_raw_pf:` (string) in host_vars.

## Memory ops should know about

- [SSH user is heterogeneous](../../.claude/projects/-home-svag-Dev/memory/project_as215932_ssh_users.md): rtr/xoa accept root, others use svag.
- [KPN ops-prefix](../../.claude/projects/-home-svag-Dev/memory/reference_kpn_ops_prefix.md): `77.166.211.126` (v4), `2a02:a442:1016::/48` (v6).
- [Live state may not match repo](../../.claude/projects/-home-svag-Dev/memory/feedback_audit_live_state.md): when adding a role, SSH-audit live state first.

## Troubleshooting

- **"vault password file not found"** — uncomment `vault_password_file` in `ansible.cfg` only after creating the encrypted `inventory/group_vars/all.vault.yml`.
- **`ansible_os_family` undefined on a host** — facts are normally gathered, but for render-only runs facts are preset in `group_vars/{linux,freebsd}.yml`.
- **Watchdog cancellation failed** — `at -l` lists pending jobs; `atrm <id>` cancels manually.
- **"_firewall_template undefined"** — fixed; the role inlines the template name in tasks/nftables.yml.
- **Host unreachable on `apply`** — SSH manually first to confirm key + user (root for rtr/xoa, svag for others).
