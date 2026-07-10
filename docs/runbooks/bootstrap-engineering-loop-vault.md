# Bootstrap Engineering Loop Vault secrets

The dedicated `loop` VM runs the Engineering Loop daemon from a narrow Vault
AppRole. This AppRole renders only GitHub App credentials, model-provider keys,
and notification credentials for the Engineering Loop runtime.

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

## Create the GitHub App

Create a GitHub App owned by `AS215932`, for example
`hyrule-engineering-loop`.

Repository access must be limited to exactly these repositories:

- `AS215932/engineering-loop`
- `AS215932/network-operations`
- `AS215932/hyrule-cloud`
- `AS215932/hyrule-web`
- `AS215932/hyrule-mcp`
- `AS215932/noc-agent`
- `AS215932/hyrule-network-proxy`
- `AS215932/as215932.net`

Required repository permissions:

- Metadata: read
- Issues: read/write
- Contents: read/write
- Pull requests: read/write

Do not grant organization administration, Actions/workflow, secrets, members,
or runner permissions.

After creating the app:

1. Generate and download one private key PEM.
2. Install the app on the eight repositories above.
3. Record the app ID and installation ID.

A quick way to discover the installation ID after installation is:

```bash
gh api /orgs/AS215932/installations \
  --jq '.installations[] | select(.app_slug=="hyrule-engineering-loop") | .id'
```

## Populate the KV payload with GitHub App credentials

Keep the downloaded PEM file on the trusted operator workstation only long
enough to write it to Vault. Do not paste the PEM into issue or PR comments.

```bash
read -rp 'GitHub App ID: ' ENGINEERING_LOOP_GITHUB_APP_ID
read -rp 'GitHub App installation ID: ' ENGINEERING_LOOP_GITHUB_APP_INSTALLATION_ID
read -rp 'Path to downloaded GitHub App private key PEM: ' ENGINEERING_LOOP_GITHUB_APP_PRIVATE_KEY_FILE

vault kv put kv/engineering-loop \
  github_app_id="$ENGINEERING_LOOP_GITHUB_APP_ID" \
  github_app_installation_id="$ENGINEERING_LOOP_GITHUB_APP_INSTALLATION_ID" \
  github_app_private_key=@"$ENGINEERING_LOOP_GITHUB_APP_PRIVATE_KEY_FILE" \
  discord_webhook="$(vault kv get -field=discord_webhook_url kv/ci-runner)" \
  icinga_url="https://mon.as215932.net:5665" \
  icinga_user="$(vault kv get -field=icinga_api_user kv/ci-runner)" \
  icinga_password="$(vault kv get -field=icinga_api_password kv/ci-runner)" \
  icinga_check="loop!engineering-loop" \
  openrouter_api_key="$(vault kv get -field=openrouter_api_key kv/noc-agent)" \
  anthropic_api_key="$(vault kv get -field=anthropic_api_key kv/noc-agent)" \
  openai_api_key="$(vault kv get -field=openai_api_key kv/noc-agent)"

unset ENGINEERING_LOOP_GITHUB_APP_ID
unset ENGINEERING_LOOP_GITHUB_APP_INSTALLATION_ID
unset ENGINEERING_LOOP_GITHUB_APP_PRIVATE_KEY_FILE
```

The `loop` VM does not store a long-lived GitHub token. Vault Agent renders the
GitHub App ID, installation ID, and private key. The systemd wrapper mints a
fresh short-lived installation token for each daemon run and exports it only to
that process tree.

## Break-glass PAT fallback

A fine-grained PAT is supported only as a temporary fallback. It must be scoped
to the same eight repositories and permissions above, and should have a short
expiration. Prefer the GitHub App path.

```bash
vault kv patch kv/engineering-loop github_token="..."
```

Do not store a broad personal `gh` OAuth token in `kv/engineering-loop`.

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
5. Confirm `/etc/engineering-loop/github-app.private-key.pem` exists with
   owner `root`, group `loop`, and mode `0640`.
6. Confirm `vault-agent-engineering-loop.service` is active.
7. Run a manual empty-queue or docs-only canary before enabling the Engineering
   daemon timer.
8. Run and inspect a real Reliability Governor dry run before enabling the
   Governor timer:
   `sudo -u loop -H /usr/local/lib/engineering-loop/run-reliability-governor --dry-run`
   The wrapper loads `/opt/engineering-loop/.env` itself as literal `KEY=value`
   data, matching the systemd unit's `EnvironmentFile=` source without
   evaluating secrets as shell syntax.
9. Keep `hyrule-engineering-loop.timer` disabled until Pi auth and the
   docs-only draft PR canary pass. Keep `hyrule-reliability-governor.timer`
   disabled until the Governor dry run shows conservative routing, healthy
   Knowledge MCP context, and successful NOC LHP fetches.

Repeat applies can run without a fresh SecretID when the Vault Agent service is
already active, no rendered Vault Agent files changed, and the rendered
destinations still exist. Any apply that changes the Vault Agent unit,
configuration, or templates still needs a fresh wrapped SecretID or an existing
unconsumed SecretID file so the service can restart safely. The token sink is
Vault Agent output state for clients, not restart bootstrap input.
No-secret repeat applies preserve the existing response-wrapped AppRole mode in
the rendered Vault Agent configuration, so omitting `VAULT_*_WRAPPED_SECRET_ID`
does not rewrite the HCL into direct-SecretID mode.

## Rollback

```bash
systemctl disable --now hyrule-reliability-governor.timer
systemctl stop hyrule-reliability-governor.service
systemctl disable --now hyrule-engineering-loop.timer
systemctl stop hyrule-engineering-loop.service
systemctl stop vault-agent-engineering-loop.service
```
