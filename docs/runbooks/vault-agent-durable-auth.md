# Vault Agent: durable AppRole auth and stale-secret alerts

How Vault Agent authenticates on AS215932 hosts, how to recover an agent that
cannot log in, and what the `vault-agent <name>` Icinga service is telling you.

Applies to every consumer of `ansible/roles/vault_agent`: `noc-agent` (noc),
`engineering-loop`, `knowledge-loop`, `agent-core-collector`,
`agentic-observatory` (loop), `hyrule-cloud` (api), `github-runner` (ci).

## The incident this exists to prevent

`vault-agent-hyrule-cloud.service` on `api` (`2a0c:b641:b50:2::20`) failed every
AppRole login with `403 permission denied` from **2026-07-18 to 2026-07-25**.

Signature:

- `journalctl -u vault-agent-hyrule-cloud` — a repeating auth-handler error loop
  (`error authenticating`, `Code: 403`, `permission denied`).
- `/run/vault-agent/hyrule-cloud.token` — **missing**. The sink is only written
  after a successful login.
- `/opt/hyrule-cloud/.env` — **mtime frozen at 18 July**. Every
  `vault kv patch kv/hyrule-cloud ...` after that date was written to Vault and
  never reached the application; the app kept running on its last good render.
- `systemctl status vault-agent-hyrule-cloud` — `active (running)` the whole
  time. The agent retries forever, so nothing crashed and nothing alerted.

It was found only because an operator patched `customer_ipv6_dns` and the value
never appeared in the running service. Seven days of silent secret drift.

Root cause: the agent was configured with `secret_id_response_wrapping_path`,
which forces `remove_secret_id_file_after_reading = true`. Vault Agent unwrapped
the SecretID once and deleted the file. The restart on 18 July therefore had no
credential at all, and no restart could ever fix itself.

## The two auth shapes

The role renders one of two `auto_auth` blocks
(`ansible/roles/vault_agent/templates/vault-agent.hcl.j2`).

| | durable (preferred) | wrapped (break-glass) |
|---|---|---|
| `vault_agent_secret_id` | plain, non-expiring SecretID | response-wrapping token |
| `vault_agent_secret_id_response_wrapping_path` | empty | `auth/approle/role/<name>/secret-id` |
| `remove_secret_id_file_after_reading` | `false` | `true` |
| Survives reboot / upgrade / OOM / deploy | **yes** | **no** |
| Credential at rest on the VM | yes (0600 root) | no |
| Compensating control | `secret_id_bound_cidrs` + `token_bound_cidrs` pinned to the host | short wrap TTL |

Durable mode trades "no long-lived credential on disk" for "the service still
works after a reboot". That trade is only acceptable with the CIDR binding: a
SecretID copied off the VM is refused by Vault because the login comes from the
wrong source address. Without the binding, do not use durable mode.

Set `vault_agent_require_durable_secret_id: true` on a consumer to enforce it.
The role then clears any wrapping path still present in the deployed `.hcl` and
refuses a response-wrapping token passed as a plain SecretID.

## Durable AppRole setup (operator, needs a Vault token)

Run from a workstation authenticated to `https://vault.as215932.net`. Replace
`<role>` and the CIDR with the values from the table below.

| consumer | role | host | bind CIDR |
|---|---|---|---|
| hyrule-cloud | `hyrule-cloud` | api | `2a0c:b641:b50:2::20/128` |
| noc-agent | `noc-agent` | noc | `2a0c:b641:b50:2::a0/128` |
| engineering-loop | `engineering-loop` | loop | `2a0c:b641:b50:2::f0/128` |
| knowledge-loop | `knowledge-loop` | loop | `2a0c:b641:b50:2::f0/128` |
| agent-core-collector | `agent-core-collector` | loop | `2a0c:b641:b50:2::f0/128` |
| agentic-observatory | `agentic-observatory` | loop | `2a0c:b641:b50:2::f0/128` |
| github-runner | `ci-runner` | ci | `2a0c:b641:b50:2::d0/128` |

Confirm the address before pasting — `ansible/inventory/hosts.yml` (`peers`) is
the source of truth.

### 1. Pin the AppRole to the host and make the SecretID non-expiring

`/128` — a single-address prefix — not the infra `/64`. The `/64` holds every
infra VM (dns, api, web, proxy, mon, noc, loop, ci, …), so binding to it would
let a SecretID stolen from `api` be replayed from any other VM on that segment;
lateral movement is exactly the risk the binding is supposed to remove. A `/128`
works because these VMs have static addresses (never DHCP) and Vault matches the
login's source IP against the list. Use a wider prefix only for a host whose
address is genuinely not fixed, and say so in the PR that does it.

Confirm the source address Vault will actually see before you bind to it — the
agent talks to the Vault VM directly over the infra `/64`, so it is the host's
address on that segment:

```bash
ssh root@2a0c:b641:b50:2::20 'ip -6 route get 2a0c:b641:b50:2::c0'
# ... src 2a0c:b641:b50:2::20 ...
```

For `hyrule-cloud` on `api`:

```bash
vault write auth/approle/role/hyrule-cloud \
    token_policies="hyrule-cloud" \
    token_ttl=30m \
    token_max_ttl=4h \
    secret_id_ttl=0 \
    secret_id_num_uses=0 \
    secret_id_bound_cidrs="2a0c:b641:b50:2::20/128" \
    token_bound_cidrs="2a0c:b641:b50:2::20/128"
```

- `secret_id_ttl=0` — the SecretID never expires, so a restart in six months
  still authenticates.
- `secret_id_num_uses=0` — unlimited uses; the agent re-reads the file on every
  start.
- `secret_id_bound_cidrs` — Vault refuses the SecretID from any other source.
- `token_bound_cidrs` — the issued token is also only usable from that address,
  so a token scraped out of the sink is equally useless elsewhere.

`vault write` on an existing role replaces the whole definition: repeat every
parameter, including `token_policies`, or you will silently drop them. Verify:

```bash
vault read auth/approle/role/hyrule-cloud
```

### 2. Mint the durable SecretID and deploy

```bash
export VAULT_HYRULE_CLOUD_ROLE_ID="$(
  vault read -field=role_id auth/approle/role/hyrule-cloud/role-id
)"
export VAULT_HYRULE_CLOUD_SECRET_ID="$(
  vault write -f -field=secret_id auth/approle/role/hyrule-cloud/secret-id
)"
# Make sure no stale wrapped value is still exported — the role refuses it, but
# an unset variable is one less thing to explain.
unset VAULT_HYRULE_CLOUD_WRAPPED_SECRET_ID

cd ansible
ansible-playbook playbooks/cloud.yml --tags apply \
  -e hyrule_cloud_apply=true \
  --limit api
```

The same shape applies to the other consumers via their own playbooks and
`VAULT_<NAME>_SECRET_ID` variables (`playbooks/noc.yml`,
`playbooks/engineering-loop.yml`, `playbooks/ci.yml`).

### 3. Revoke the SecretIDs the old shape left behind

After a durable SecretID is in place, drop the accessors from earlier
bootstraps so only the live credential can log in:

```bash
vault list auth/approle/role/hyrule-cloud/secret-id
# for each accessor that is not the one you just minted:
vault write auth/approle/role/hyrule-cloud/secret-id-accessor/destroy \
    secret_id_accessor="<accessor>"
```

## Wrapped mode: stop the agent first

If you deliberately keep a consumer on response wrapping, the ordering matters.
An agent that is already in its retry loop unwraps the token the instant the
file appears, so the `systemctl restart` you do afterwards finds a spent token
and dies with `wrapping token is not valid or does not exist`. That trap cost an
extra recovery cycle on 25 July.

```bash
ssh root@<host> systemctl stop vault-agent-<name>
# install the fresh wrapped SecretID (ansible apply, or by hand)
ssh root@<host> systemctl start vault-agent-<name>
```

`ansible/roles/vault_agent` now does this automatically: with
`vault_agent_stop_before_wrapped_secret_id_install: true` (default) it stops the
unit before writing a wrapped credential and starts it afterwards, so the unwrap
happens exactly once.

## Monitoring

`ansible/roles/vault_agent` installs `vault-agent-health-metrics.timer` on every
host running an agent. It runs every 5 minutes, inspects **all** agents
configured in `/etc/vault-agent.d/`, and writes
`/var/lib/node_exporter/textfile_collector/vault_agent.prom`. node_exporter
exposes it (`--collector.textfile.directory`, enabled by
`ansible/roles/monitoring`), Prometheus on mon scrapes it, and the
`vault-agent <name>` Icinga service evaluates it
(`configs/mon/icinga2/services/vault-agent.conf`,
`configs/mon/icinga2/scripts/check_vault_agent_health.py`).

| metric | meaning |
|---|---|
| `vault_agent_token_sink_present` / `_mtime_seconds` | sink file; rewritten on every successful login |
| `vault_agent_auth_errors_recent` | auth failures in the agent journal over the last 15m (`-1` = journal unreadable) |
| `vault_agent_secret_id_file_present` | the SecretID file still exists |
| `vault_agent_secret_id_ephemeral` | `1` when `remove_secret_id_file_after_reading = true` |
| `vault_agent_render_present` / `_mtime_seconds` | per rendered destination |
| `vault_agent_collector_run_timestamp_seconds` | freshness of the sample itself |

Alert states:

| state | condition | meaning |
|---|---|---|
| CRITICAL | no token sink | the agent holds no Vault token — the July signature |
| CRITICAL | token sink older than 6h | re-authentication has stopped |
| CRITICAL | auth errors in the journal | the 403 loop, caught within minutes |
| CRITICAL | agent unit inactive | |
| CRITICAL | a rendered destination is missing | |
| WARNING | `_ephemeral=1` and the SecretID file is gone | single-use credential already consumed; the agent works **until the next restart**, then 403s forever. Migrate it to durable mode |
| WARNING | destination older than `vault_agent_render_max_age` | opt-in only, see below |
| UNKNOWN | no metrics, or sample older than 15m | the collector is down; agent state is unverified |

Threshold notes:

- 6h for the token sink clears the 4h `token_max_ttl` used fleet-wide plus one
  missed cycle. The sink is rewritten on re-authentication, not on renewal, so a
  healthy agent touches it every ≤4h.
- Render age is **off by default** (`vault_agent_render_max_age = 0`). Vault
  Agent only writes a destination when the rendered content changes, so a stable
  secret legitimately keeps an old mtime and any fixed threshold would be a
  false alarm. Turn it on per host only where the secret rotates on a schedule.
  A *missing* destination is always CRITICAL.
- Per-host overrides go in `monitoring_check_vars`
  (`vault_agent_ephemeral_state`, `vault_agent_token_max_age`,
  `vault_agent_render_max_age`).

## Recovery: agent cannot authenticate

```bash
ssh root@<host> systemctl status vault-agent-<name>          # likely "active"
ssh root@<host> journalctl -u vault-agent-<name> -n 50       # 403 / permission denied
ssh root@<host> ls -l /run/vault-agent/<name>.token          # missing?
ssh root@<host> ls -l /etc/vault-agent.d/<name>-secret-id    # missing => consumed
ssh root@<host> ls -l <rendered destination>                 # mtime frozen?
```

Then mint a durable SecretID (steps 1–2 above) and re-run the consumer's apply
playbook. Do not hand it another wrapped token: that restores the trap.

### Verify recovery

```bash
ssh root@<host> ls -l /run/vault-agent/<name>.token   # exists, mtime = now
ssh root@<host> ls -l /etc/vault-agent.d/<name>-secret-id  # persists after start
ssh root@<host> ls -l <rendered destination>          # mtime updated if content changed
ssh root@<host> systemctl restart vault-agent-<name>  # THE test: must survive it
ssh root@<host> journalctl -u vault-agent-<name> -n 20   # "authentication successful"
```

The restart is the point of the whole exercise — an agent that only works until
its next restart is the failure this runbook exists to eliminate. Confirm the
token sink reappears after it.

End-to-end proof that patches reach the app again:

```bash
vault kv patch kv/hyrule-cloud <key>=<value>
ssh root@2a0c:b641:b50:2::20 'ls -l /opt/hyrule-cloud/.env'   # mtime bumps within ~10s
```

In Icinga, the `vault-agent <name>` service returns to OK within one check
interval (5m) of the next collector run.

## Related

- [bootstrap-hyrule-cloud-vault.md](bootstrap-hyrule-cloud-vault.md)
- [bootstrap-runner-vault.md](bootstrap-runner-vault.md)
- [bootstrap-engineering-loop-vault.md](bootstrap-engineering-loop-vault.md)
- [bootstrap-knowledge-loop-vault.md](bootstrap-knowledge-loop-vault.md)
- [bootstrap-agent-core-collector-vault.md](bootstrap-agent-core-collector-vault.md)
- [bootstrap-agentic-observatory-vault.md](bootstrap-agentic-observatory-vault.md)
