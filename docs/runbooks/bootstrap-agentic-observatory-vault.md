# Bootstrap Agentic Observatory Vault scope

Agentic Observatory runs on `loop` as an internal operator-only console. Its Vault scope is `kv/agentic-observatory` and AppRole `agentic-observatory`.

## Required KV keys

Write these keys without logging secret values:

- `session_secret`
- `csrf_secret`
- `operator_username` / `operator_password_hash` (development or explicit
  break-glass only; production local login is disabled by default)
- `db_password` (URL-safe for the SQLAlchemy database URL)
- `noc_loop_console_secret` (must match NOC `NOC_LOOP_CONSOLE_SECRET`)
- `github_token` (read-only GitHub token/App installation token; required for the private runtime checkout and runtime GitHub context)
- `github_oauth_client_id` / `github_oauth_client_secret`
- `github_oauth_policy_token` (fine-grained token owned by an organization
  owner, used only to read the organization 2FA policy)
- `coordinator_secret` (only the `observatory/v1` HMAC key)
- `collector_ingest_token` when InsightLabel writes are enabled

## Policy/apply order

1. Apply the workload policy:
   ```sh
   vault policy write agentic-observatory configs/vault/policies/agentic-observatory.hcl
   ```
2. Refresh the runner policy before the first deploy so apply.yml can mint a wrapped SecretID:
   ```sh
   vault policy write github-runner configs/vault/policies/github-runner.hcl
   ```
3. Create/bind AppRole `agentic-observatory` to the `agentic-observatory` policy.
4. Run the `engineering-loop` playbook with `agentic_observatory_apply=true` (apply.yml adds this automatically for live engineering-loop applies).

Before promoting the OAuth build, configure its callback URL as
`https://observatory.servify.network/auth/github/callback`, enforce 2FA on
`AS215932`, and create the `ops` team. Verify an ops member maps to `operator`,
an owner maps to `senior`, and a non-ops member is denied. Keep handoff actions
off until the dual-read shadow stage passes.
