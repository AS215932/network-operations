# Bootstrap the Agent Core coordinator Vault scope

The central LHP-v2 coordinator runs on `loop`, separately from the trace
collector, and reads only `kv/agent-core-coordinator`. Its database password
and per-identity HMAC keys never enter inventory.

```bash
vault policy write agent-core-coordinator configs/vault/policies/agent-core-coordinator.hcl
vault write auth/approle/role/agent-core-coordinator \
  token_policies="agent-core-coordinator" \
  token_ttl=1h token_max_ttl=4h \
  secret_id_ttl=24h secret_id_num_uses=1
```

Generate independent 32-byte-or-longer secrets for `soc`, `noc`,
`engineering`, `knowledge`, and `observatory`. Store the compact JSON object
as one Vault value:

```bash
vault kv put kv/agent-core-coordinator \
  db_password='...' \
  database_url='postgresql+asyncpg://agent_core_coordinator:URL_ENCODED_PASSWORD@127.0.0.1/agent_core_coordinator' \
  loop_keys_json='{"soc":{"v1":"..."},"noc":{"v1":"..."},"engineering":{"v1":"..."},"knowledge":{"v1":"..."},"observatory":{"v1":"..."}}'
```

Copy only the matching secret to each workload's existing KV scope as
`coordinator_secret`. A loop must never receive another loop's key. The
Observatory key is read/control-plane only and cannot claim loop work.

The trusted runner can mint a response-wrapped coordinator AppRole SecretID,
but its policy cannot read the key JSON. Apply only after `agent-core` is
merged and `agent_core_coordinator_version` is promoted to that exact SHA.

```bash
vault read -field=role_id auth/approle/role/agent-core-coordinator/role-id
vault write -wrap-ttl=10m -f auth/approle/role/agent-core-coordinator/secret-id
ssh loop 'systemctl status vault-agent-agent-core-coordinator agent-core-coordinator --no-pager'
curl -g 'http://[2a0c:b641:b50:2::f0]:8771/healthz'
```
