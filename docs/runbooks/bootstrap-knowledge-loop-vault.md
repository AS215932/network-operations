# Bootstrap Knowledge Loop Vault credentials

Knowledge Loop is a dedicated producer agent for `AS215932/knowledge`. It is not
Engineering Loop, not CI/CD PR-Agent, and not the read-only Knowledge MCP server.

## Install the policy

```bash
vault policy write knowledge-loop configs/vault/policies/knowledge-loop.hcl
```

## Create the AppRole

```bash
vault write auth/approle/role/knowledge-loop \
  token_policies="knowledge-loop" \
  token_ttl=1h \
  token_max_ttl=4h \
  secret_id_ttl=24h \
  secret_id_num_uses=0
```

## Store Knowledge Loop runtime secrets

Use a dedicated GitHub App if possible. The app should be scoped to the
Knowledge repository for contents/pull-requests/issues as needed by the loop.
Do not reuse the Engineering Loop runtime key or CI/CD PR-Agent key.

```bash
vault kv put kv/knowledge-loop \
  github_app_id="..." \
  github_app_installation_id="..." \
  github_app_private_key=@/path/to/knowledge-loop-app.pem \
  openrouter_api_key="..." \
  create_pr="1" \
  enrich_live="0" \
  max_openrouter_calls_per_day="0" \
  learning_event_paths="" \
  icinga_url="https://localhost:5665" \
  icinga_user="..." \
  icinga_password="..." \
  icinga_check="loop!knowledge-loop"
```

Fallback token mode is supported for break-glass only:

```bash
vault kv patch kv/knowledge-loop github_token="..."
```

## First deploy bootstrap

The trusted CI runner can mint a response-wrapped SecretID for `knowledge-loop`
when `apply.yml` runs with `playbook=engineering-loop`. Manual equivalent:

```bash
vault read -field=role_id auth/approle/role/knowledge-loop/role-id
vault write -wrap-ttl=10m -f auth/approle/role/knowledge-loop/secret-id
```

Pass those as:

- `VAULT_KNOWLEDGE_LOOP_ROLE_ID`
- `VAULT_KNOWLEDGE_LOOP_WRAPPED_SECRET_ID`

## Safety checks

After apply on `loop`:

```bash
systemctl status vault-agent-knowledge-loop.service
systemctl status hyrule-knowledge-loop.timer
sudo test -s /etc/knowledge-loop/knowledge-loop.env
sudo test -s /etc/knowledge-loop/github-app.private-key.pem
```

Keep `hyrule-knowledge-loop.timer` disabled until the canary PR pins a reviewed
Knowledge commit containing `hyrule-knowledge loop --once` and operators approve
the first run.
