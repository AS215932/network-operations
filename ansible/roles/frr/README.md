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
6. Moves the new config into place, **reloads** (FRR integrated delta-reload —
   no restart), then **`clear bgp ipv6 unicast * soft`** to re-apply policy.
7. Cancels the watchdog once the reload completes cleanly.

The reload runs on **every** apply (not gated on the file changing): `frr-reload`
diffs against the *running daemon*, so a converged daemon is a cheap no-op, while
a daemon left stale by a prior run still gets converged. This avoids the trap
where the on-disk file already matches the repo but the daemon never ingested it.

`serial: 1` and the pre/post Icinga snapshot bracket are on the playbook
(`playbooks/frr.yml`), matching the `firewall` role.

## OS differences (`vars/<os_family>.yml`)

| | FreeBSD (cr1-nl1, cr1-de1) | Debian (rtr) |
|---|---|---|
| `frr_conf_path` | `/usr/local/etc/frr/frr.conf` | `/etc/frr/frr.conf` |
| `frr_reload_cmd` | `frr-reload.py --reload …` (direct) | `systemctl reload frr` |
| `frr_validate_cmd` | `vtysh -C -f` | `vtysh -C -f` |

> FreeBSD's `service frr reload` does **not** invoke FRR's integrated reload — on
> first use it silently applied nothing. So the FreeBSD `frr_reload_cmd` calls
> `/usr/local/lib/frr/frr-reload.py --reload --bindir /usr/local/bin --confdir
> /usr/local/etc/frr --stdout /usr/local/etc/frr/frr.conf` directly (the same
> tool Debian's `systemctl reload frr` runs internally). Confirmed working on
> cr1-de1 (FRR 10.4.1 / FreeBSD 15). Always verify the change actually took with
> `vtysh -c 'show route-map …'` after — a reload returning rc 0 is not proof the
> running config converged.

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
