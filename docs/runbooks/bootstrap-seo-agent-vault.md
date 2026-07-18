# Bootstrap the Hyrule Beacon worker Vault scope

The `seo-agent` service runs on `loop`, polls Hyrule Beacon, and stores only
LangGraph/SQLite state locally. Its Vault scope is `kv/seo-agent`; it receives
no GitHub token, wallet, registry publishing credential, or infrastructure
secret.

## Prerequisites

- Publish `AS215932/hyrule-seo-agent` and ensure the SHA pinned as
  `seo_agent_version` is reachable. The deployment never follows a branch.
- Create a dedicated, revocable production worker credential through
  `POST /api/v1/beacon/workers`. Keep the returned plaintext token only long
  enough to put it in Vault.
- Keep `seo_agent_execute_automatic_actions: false` for the initial rollout.

## Create the KV value and AppRole

```bash
vault kv put kv/seo-agent \
  beacon_worker_token='beacon_worker_…' \
  indexnow_key='' \
  openrouter_api_key='' \
  psi_api_key='' \
  umami_api_token='' \
  umami_website_id='' \
  discord_webhook_url=''

vault policy write seo-agent configs/vault/policies/seo-agent.hcl
vault write auth/approle/role/seo-agent \
  token_policies=seo-agent \
  token_ttl=1h \
  token_max_ttl=4h \
  secret_id_ttl=10m \
  secret_id_num_uses=1

# Refresh the trusted runner policy before the first engineering-loop apply.
vault policy write github-runner configs/vault/policies/github-runner.hcl
```

The apply workflow mints a 10-minute response-wrapped SecretID. The target-side
Vault Agent unwraps it and renders `/etc/seo-agent/seo-agent.env`; the CI runner
cannot read `kv/seo-agent`.

## Apply and verify

After the worker repository and pin exist remotely, run the normal gated
`engineering-loop` apply. Then verify:

```bash
ssh loop 'systemctl status vault-agent-seo-agent seo-agent --no-pager'
ssh loop 'curl -fsS "http://[2a0c:b641:b50:2::f0]:8790/health"'
ssh loop 'sudo docker inspect seo-agent --format "{{.Config.Image}}"'
```

The health response must report `beacon_managed_mode: true`,
`beacon_configured: true`, and no persistent `beacon_last_error`. Automatic
actions remain off until a separate reviewed rollout enables them.

## Rotate or revoke

Create a replacement Beacon credential, patch `beacon_worker_token` in Vault,
wait for Vault Agent to restart the service, and then revoke the old credential
in Beacon. A leaked token grants worker leasing only; it does not grant operator
API access.
