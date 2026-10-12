# Bootstrap `agent-core-collector` Vault access

The Agent-Core trace collector runs on `loop` as `agent-core-collector.service` and
stores events in local Postgres. Its only secret is the local Postgres password,
rendered by Vault Agent to `/etc/agent-core-collector/collector.env` from
`kv/agent-core-collector`.

Run this once before merging/applying the collector deploy PR, then repeat the
`vault policy write ...` commands whenever the policy files change.

## Prerequisites

- Admin/root Vault token in `VAULT_TOKEN`.
- Run from `network-operations` repo root.
- Internal Vault listener reachable over the management overlay.

```bash
export VAULT_ADDR="http://[2a0c:b641:b50:2::c0]:8200"
# export VAULT_TOKEN=<admin token>
```

## Bootstrap

```bash
collector_db_password="$(openssl rand -hex 32)"

vault policy write agent-core-collector configs/vault/policies/agent-core-collector.hcl

vault write auth/approle/role/agent-core-collector \
  token_policies="agent-core-collector" \
  token_ttl=1h \
  token_max_ttl=4h \
  secret_id_ttl=24h \
  secret_id_num_uses=0

vault kv put kv/agent-core-collector \
  db_password="$collector_db_password"

# Let the trusted CI runner mint only response-wrapped SecretIDs for this
# target-side AppRole during engineering-loop applies.
vault policy write github-runner configs/vault/policies/github-runner.hcl
```

The deploy workflow will read the collector AppRole role_id and mint a
10-minute response-wrapped SecretID, then the `vault_agent` role on `loop` will
unwrap it and render `/etc/agent-core-collector/collector.env`.

## Verify after apply

```bash
ssh loop 'sudo ls -l /etc/agent-core-collector/collector.env'
ssh loop 'systemctl status vault-agent-agent-core-collector --no-pager'
ssh loop 'systemctl status agent-core-collector --no-pager'
ssh loop 'curl -fsS "http://[2a0c:b641:b50:2::f0]:8770/healthz"'
ssh loop 'sudo -u postgres psql agent_core_collector -c "\\dt"'
```

Expected health response:

```json
{"status":"ok"}
```

## Rotate the collector DB password

```bash
new_password="$(openssl rand -hex 32)"
vault kv patch kv/agent-core-collector db_password="$new_password"
```

Vault Agent should re-render `collector.env` within its wait window and
`try-restart` the collector service. The next `engineering-loop` apply will also
rotate the local Postgres role password to the rendered env value.
