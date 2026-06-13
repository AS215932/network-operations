# Bootstrap: CI runner Vault AppRole

One-time setup so the `ci` VM's Vault Agent can render
`/etc/github-runner/secrets.env`, which `.github/workflows/apply.yml` sources
for `ansible-playbook --tags apply` runs.

This is a prerequisite for the first `playbooks/ci.yml --tags apply` that
includes the `vault_agent` role (i.e. after the PR that wires it in lands).
Without it, the `vault_agent` role's bootstrap assertion fails because no
AppRole credentials are present.

## Prerequisites

- `vault` CLI authenticated against `https://vault.as215932.net` with a token
  that can write policies and manage the AppRole auth mount.
- The `ci` VM provisioned and the runner registered (see
  [docs/ci/provision.md](../ci/provision.md)).

## 1. Write the policy

The policy file is version-controlled at
[configs/vault/policies/github-runner.hcl](../../configs/vault/policies/github-runner.hcl).

```bash
vault policy write github-runner configs/vault/policies/github-runner.hcl
```

## 2. Create the AppRole

```bash
# AppRole auth is already mounted (noc-agent uses it). Create the ci-runner role.
vault write auth/approle/role/ci-runner \
    token_policies="github-runner" \
    token_ttl=1h \
    token_max_ttl=4h \
    secret_id_ttl=0 \
    secret_id_num_uses=0
```

`secret_id_ttl=0` keeps the secret_id non-expiring — the runner is a
long-lived host, not an ephemeral workload. If you prefer rotation, set a
finite TTL and record the next rotation date here so it doesn't expire
silently.

## 3. Populate the KV entry

Vault Agent renders from `kv/data/ci-runner` (see
[github-runner.env.ctmpl.j2](../../ansible/roles/vault_agent/templates/github-runner.env.ctmpl.j2)).
Seed it with the secrets apply runs currently consume:

```bash
vault kv put kv/ci-runner \
    discord_webhook_url="https://discord.com/api/webhooks/..." \
    icinga_api_user="..." \
    icinga_api_password="..." \
    icinga_noc_agent_api_password="..." \
    network_proxy_token="..."
```

Do **not** store `XO_TOKEN` or `XCPNG_XO_TOKEN` here. Hyrule Cloud receives
XCP-ng/Openprovider/payment/database secrets through its own target-side Vault
Agent on the `api` VM; see
[bootstrap-hyrule-cloud-vault.md](./bootstrap-hyrule-cloud-vault.md).

When a playbook enters CI scope and needs another runner-scoped secret, add
the key here and a matching line in the ctmpl template — keep both in lockstep.

## 4. Fetch role_id + secret_id

```bash
vault read -field=role_id   auth/approle/role/ci-runner/role-id
vault write -f -field=secret_id auth/approle/role/ci-runner/secret-id
```

Put both in `secrets.local.sh` so the next apply picks them up:

```sh
export VAULT_CI_RUNNER_ROLE_ID=...
export VAULT_CI_RUNNER_SECRET_ID=...
```

## 5. Apply

```bash
cd ansible
set -a; source ../secrets.local.sh; set +a
ansible-playbook playbooks/ci.yml --tags apply \
    -e github_runner_apply=true --limit ci
```

The `vault_agent` role installs `vault-agent-github-runner.service`, writes
the AppRole files under `/etc/vault-agent.d/`, and renders
`/etc/github-runner/secrets.env`. After the first apply the AppRole files
persist on the host, so subsequent applies converge without the env vars.

## Verify

```bash
ssh root@2a0c:b641:b50:2::d0 systemctl status vault-agent-github-runner
ssh root@2a0c:b641:b50:2::d0 'ls -l /etc/github-runner/secrets.env'   # 0640 runner:runner
ssh root@2a0c:b641:b50:2::d0 '/path/to/network-operations/scripts/ci/deploy-preflight.sh --runner'
```

Then run the `apply.yml` smoke test from
[docs/ci/deploy-runbook.md](../ci/deploy-runbook.md) — the "Source
Vault-rendered secrets" step should find the file and not emit the
`secrets.env not found` warning.
