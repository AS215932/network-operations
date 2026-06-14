# Bootstrap Engineering Loop Vault secrets

The dedicated `loop` VM runs the Engineering Loop daemon from a narrow Vault
AppRole. This AppRole must only render the daemon's GitHub issue/PR token,
model-provider credentials, and notification credentials into
`/opt/engineering-loop/.env`.

Do **not** place fleet SSH keys, broad Vault tokens, XO credentials, registrar
credentials, or application runtime secrets in `kv/engineering-loop`.

## Preconditions

- You are an authorized Vault operator.
- You are running from a trusted operator host or the trusted `ci` runner.
- The `engineering-loop` policy in this repository has been reviewed.
- The `github-runner` policy has been updated in live Vault before relying on
  `apply.yml` to mint a response-wrapped SecretID for the `loop` VM.

```bash
export VAULT_ADDR='http://[2a0c:b641:b50:2::c0]:8200'
vault token lookup
```

## Install the engineering-loop policy

```bash
vault policy write engineering-loop configs/vault/policies/engineering-loop.hcl
```

## Create or update the AppRole

```bash
vault write auth/approle/role/engineering-loop \
  token_policies="engineering-loop" \
  token_ttl=1h \
  token_max_ttl=24h \
  secret_id_ttl=0 \
  secret_id_num_uses=0
```

## Populate the KV payload

Use a fine-grained GitHub PAT or GitHub App installation token scoped only to
these repositories:

- `AS215932/engineering-loop`
- `AS215932/network-operations`
- `AS215932/hyrule-cloud`
- `AS215932/hyrule-web`
- `AS215932/hyrule-mcp`
- `AS215932/noc-agent`
- `AS215932/hyrule-network-proxy`

Required GitHub permissions:

- Metadata: read
- Issues: read/write
- Contents: read/write
- Pull requests: read/write

No admin or org-wide permissions are required.

```bash
vault kv put kv/engineering-loop \
  github_token="..." \
  discord_webhook="..." \
  icinga_url="https://[2a0c:b641:b50:2::50]:5665/v1/actions/process-check-result" \
  icinga_user="..." \
  icinga_password="..." \
  icinga_check="loop!engineering-loop" \
  openrouter_api_key="..." \
  anthropic_api_key="..." \
  openai_api_key="..."
```

## Refresh the ci runner policy

After merging the production cutover PR, apply the updated `github-runner`
policy so `apply.yml` can mint a short-lived response-wrapped SecretID for the
`engineering-loop` AppRole:

```bash
vault policy write github-runner configs/vault/policies/github-runner.hcl
```

## Verify without exposing secrets

```bash
vault kv metadata get kv/engineering-loop
vault read auth/approle/role/engineering-loop/role-id
vault write -wrap-ttl=10m -f auth/approle/role/engineering-loop/secret-id
```

Do not unwrap SecretIDs in logs or paste them into issue/PR comments.

## Rollout

Use the production workflow after the policies and KV entry exist:

1. Run `apply.yml` with `playbook=engineering-loop`, `limit=loop`,
   `dry_run=true`.
2. Run `apply.yml` with `playbook=engineering-loop`, `limit=loop`,
   `dry_run=false`.
3. Approve the GitHub `production` environment gate.
4. Confirm `/opt/engineering-loop/.env` exists on `loop` with owner/root and
   group access for the `loop` service only.
5. Confirm `vault-agent-engineering-loop.service` is active.
6. Keep `hyrule-engineering-loop.timer` disabled until Pi auth and the
   docs-only draft PR canary pass.

## Rollback

```bash
systemctl disable --now hyrule-engineering-loop.timer
systemctl stop hyrule-engineering-loop.service
systemctl stop vault-agent-engineering-loop.service
```
