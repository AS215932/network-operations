# Bootstrap Agentic Observatory Vault scope

Agentic Observatory runs on `loop` as an internal operator-only console. Its Vault scope is `kv/agentic-observatory` and AppRole `agentic-observatory`.

## Required KV keys

Write these keys without logging secret values:

- `session_secret`
- `csrf_secret`
- `operator_username`
- `operator_password_hash` (Argon2 hash)
- `db_password` (URL-safe for the SQLAlchemy database URL)
- `noc_loop_console_secret` (must match NOC `NOC_LOOP_CONSOLE_SECRET`)
- `github_token` (read-only GitHub token/App installation token; required for the private runtime checkout and runtime GitHub context)

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

Keep the service in read-only mode until the read-only rollout verification passes.
