# frr role

Deploys the committed FRRouting config (`configs/<host>/frr.conf`) to a router
and hot-reloads it. The repo's `configs/<host>/frr.conf` stays the single source
of truth — this role does **not** template/render it; it pushes the file verbatim
and applies a delta-reload so iBGP/OSPF sessions are not flapped.

## What it does (on `--tags apply` + `frr_apply=true`)

1. Asserts `configs/<host>/frr.conf` exists (the validate/dry-run stops here).
2. Stages it to `<conf>.new` on the host.
3. Syntax-checks the staged file (`vtysh -C -f`).
4. Backs up the currently-loaded config.
5. Schedules an `at(1)` watchdog (default 5 min) that restores + reloads the
   backup if the play does not cancel it — covers a lockout from a bad policy.
6. Moves the new config into place and fires the handler chain:
   **validate → reload → `clear bgp ipv6 unicast * soft`**.
7. Cancels the watchdog once the reload completes cleanly.

`serial: 1` and the pre/post Icinga snapshot bracket are on the playbook
(`playbooks/frr.yml`), matching the `firewall` role.

## OS differences (`vars/<os_family>.yml`)

| | FreeBSD (cr1-nl1, cr1-de1) | Debian (rtr) |
|---|---|---|
| `frr_conf_path` | `/usr/local/etc/frr/frr.conf` | `/etc/frr/frr.conf` |
| `frr_reload_cmd` | `service frr reload` | `systemctl reload frr` |
| `frr_validate_cmd` | `vtysh -C -f` | `vtysh -C -f` |

> The FreeBSD `frr_reload_cmd` (`service frr reload`) assumes the frr port's rc
> script wires a `reload` verb. If a first apply shows it does not, override in
> `host_vars`/`group_vars` to call frr-reload directly:
> `/usr/local/lib/frr/frr-reload.py --reload --bindir /usr/local/bin --confdir /usr/local/etc/frr /usr/local/etc/frr/frr.conf`.
> The syntax check + backup + watchdog make a wrong reload command safe (it
> reverts), but confirm it before relying on the pipeline for FreeBSD.

## Key variables (`defaults/main.yml`)

- `frr_apply` (false) — push + reload, or validate-only.
- `frr_clear_bgp` (true) / `frr_clear_bgp_cmd` — soft policy re-eval after reload.
- `frr_watchdog_minutes` (5) — rollback window.

## Usage

```bash
cd ansible
# Validate-only (no host connection, no change):
ansible-playbook playbooks/frr.yml --tags validate --connection=local --skip-tags=snapshot

# Apply to one router (Icinga-bracketed, serial:1):
ansible-playbook playbooks/frr.yml --tags apply --limit rtr -e frr_apply=true
```

Or via the gated workflow:
`gh workflow run apply.yml -F playbook=frr -F limit=rtr -F dry_run=false`.
