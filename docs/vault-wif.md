# Vault-backed WIF for NOC Agent

This is the v1 secret architecture for the NOC Agent:

- Vault stores machine and application secrets.
- Vault Agent renders the existing `/opt/noc-agent/.env`, so app code does not call Vault.
- Vault issues a short-lived OIDC identity token at `identity/oidc/token/google-wif-noc-agent`.
- Google Workload Identity Federation exchanges that token for a short-lived Google access token.
- `GOOGLE_APPLICATION_CREDENTIALS=/etc/noc-agent/google-wif.json` makes the standard Google client libraries use ADC without a static service-account key.

## One-time rollout

1. Provision the `vault` VM at `2a0c:b641:b50:2::c0`.

2. Install Vault:

   ```bash
   ansible-playbook playbooks/vault.yml --tags apply --limit vault
   ```

3. Initialize and unseal Vault manually. Store unseal material outside git.

4. Bootstrap Vault and Google WIF from an operator workstation:

   ```bash
   export VAULT_ADDR=https://vault.as215932.net
   export VAULT_TOKEN=...
   # Or authenticate with: vault login
   ./scripts/bootstrap-vault-noc-wif.sh
   ```

5. Load current NOC secrets into Vault:

   ```bash
   export VAULT_ADDR=https://vault.as215932.net
   export VAULT_TOKEN=...
   # Or authenticate with: vault login
   ./scripts/vault-put-noc-agent-secrets.sh
   ```

6. Deploy NOC. Vault Agent is the production default:

   ```bash
   export VAULT_NOC_AGENT_ROLE_ID=...
   export VAULT_NOC_AGENT_SECRET_ID=...
   ansible-playbook playbooks/noc.yml --tags apply -e '{"noc_apply":true}' --limit noc
   ```

The AppRole `role_id` and `secret_id` are bootstrap credentials. They are written root-only on the NOC VM and are not committed.

7. Take an encrypted raft snapshot after bootstrap and after major secret changes:

   ```bash
   export VAULT_ADDR=https://vault.as215932.net
   export VAULT_TOKEN=...
   # Or authenticate with: vault login
   export VAULT_SNAPSHOT_AGE_RECIPIENT=age1...
   ./scripts/vault-raft-snapshot.sh
   ```

## Vault paths

`kv/noc-agent` uses these keys:

- `gemini_api_key`
- `anthropic_api_key`
- `openai_api_key`
- `discord_webhook_url`
- `discord_bot_token`
- `noc_control_token`
- `noc_approval_signing_secret`
- `mail_imap_password`
- `xo_token`
- `icinga_api_user`
- `icinga_api_password`

Future service paths should follow the same split:

- `kv/hyrule-cloud`
- `kv/dns`
- `kv/mail`
- `kv/ripe`
- `kv/xcpng`
- `kv/extmon`

Keep non-secrets such as UUIDs, hostnames, handles, project IDs, wallet addresses, and model names in inventory/config.

`secrets.local.sh` is a workstation bootstrap/import source, not the
production runtime source for NOC Agent. Re-run
`scripts/vault-put-noc-agent-secrets.sh` after rotating any NOC secret so Vault
remains authoritative.

## Validation

On NOC:

```bash
sudo systemctl status vault-agent-noc-agent
sudo test -s /opt/noc-agent/.env
sudo test -s /run/noc-agent/google-subject.jwt
sudo test -s /etc/noc-agent/google-wif.json
curl -s http://[2a0c:b641:b50:2::a0]:8000/health/model
```

Expected result: `/health/model` reports quota monitoring as `ok` once Cloud Monitoring has data and ADC can exchange the Vault token.

## Security notes

- Do not create Google service-account JSON keys. The organization policy blocks them intentionally.
- Do not commit Vault tokens, AppRole secret IDs, rendered env files, or generated WIF subject tokens.
- Vault audit logs are enabled by bootstrap and must be protected like sensitive operational logs.
- Keycloak remains a later human-SSO layer; it is not the v1 application secret store.
