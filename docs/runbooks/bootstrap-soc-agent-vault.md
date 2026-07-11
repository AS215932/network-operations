# Bootstrap the dedicated SOC Agent Vault scope

The SOC VM is `2a0c:b641:b50:2::100`. Its AppRole reads only
`kv/soc-agent`; it has no production remediation, fleet SSH, registrar,
wallet, XO, or NOC action-signing credentials.

```bash
vault policy write soc-agent configs/vault/policies/soc-agent.hcl
vault write auth/approle/role/soc-agent \
  token_policies="soc-agent" \
  token_ttl=1h token_max_ttl=4h \
  secret_id_ttl=24h secret_id_num_uses=1

vault kv put kv/soc-agent \
  db_password='...' \
  database_url='postgresql://soc_agent:URL_ENCODED_PASSWORD@127.0.0.1/soc_agent' \
  coordinator_secret='the-soc-v1-key-only' \
  control_token='...' \
  openrouter_api_key='' \
  discord_webhook_url=''
```

The CI apply workflow mints a single-use response-wrapped SecretID. Before the
first apply, provision the NoCloud seed under `autoinstall/generated/soc/`,
install the CI deploy key, merge/promote the SOC app SHA, and keep every SOC
timer disabled. First apply establishes PostgreSQL, Vault Agent, firewall,
logging, and monitoring only.

```bash
gh workflow run apply.yml -f playbook=soc -f limit=soc -f dry_run=false
ssh soc 'systemctl status vault-agent-soc-agent postgresql --no-pager'
```

Enable workloads only through the shadow-cutover runbook. Never place NOC MCP
mutation credentials on this VM.
