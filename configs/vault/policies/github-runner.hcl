# Vault policy for the CI runner.
# Deploy path: applied to Vault as policy `github-runner`, bound to the
# AppRole role `ci-runner`. See docs/runbooks/bootstrap-runner-vault.md.
#
# Grants read-only access to the single KV entry Vault Agent on the `ci` VM
# renders into /etc/github-runner/secrets.env for apply.yml runs. Scope is
# deliberately narrow — the runner can apply playbooks but cannot read any
# other workload's secrets.

path "kv/data/ci-runner" {
  capabilities = ["read"]
}

path "kv/metadata/ci-runner" {
  capabilities = ["read"]
}

# Production cloud applies run on the trusted ci runner and need to bootstrap
# the api VM's target-side Vault Agent. The runner may mint only a short-lived,
# response-wrapped SecretID for the hyrule-cloud AppRole; it still cannot read
# kv/hyrule-cloud runtime secrets.
path "auth/approle/role/hyrule-cloud/role-id" {
  capabilities = ["read"]
}

path "auth/approle/role/hyrule-cloud/secret-id" {
  capabilities = ["update"]
}

# Production engineering-loop applies run on the trusted ci runner and need to
# bootstrap the loop VM's target-side Vault Agent. The runner may mint only a
# short-lived, response-wrapped SecretID for the engineering-loop AppRole; it
# still cannot read kv/engineering-loop runtime secrets.
path "auth/approle/role/engineering-loop/role-id" {
  capabilities = ["read"]
}

path "auth/approle/role/engineering-loop/secret-id" {
  capabilities = ["update"]
}

# Production knowledge-loop applies run on the trusted ci runner and need to
# bootstrap the loop VM's target-side Vault Agent. The runner may mint only a
# short-lived, response-wrapped SecretID for the knowledge-loop AppRole; it
# still cannot read kv/knowledge-loop runtime secrets.
path "auth/approle/role/knowledge-loop/role-id" {
  capabilities = ["read"]
}

path "auth/approle/role/knowledge-loop/secret-id" {
  capabilities = ["update"]
}

# Production engineering-loop applies also bootstrap the loop-host
# agent-core-collector Vault Agent. The runner may mint only a short-lived,
# response-wrapped SecretID for the collector AppRole; it still cannot read
# kv/agent-core-collector runtime secrets.
path "auth/approle/role/agent-core-collector/role-id" {
  capabilities = ["read"]
}

path "auth/approle/role/agent-core-collector/secret-id" {
  capabilities = ["update"]
}

# Production engineering-loop applies also bootstrap the loop-host
# agentic-observatory Vault Agent. The runner may mint only a short-lived,
# response-wrapped SecretID for the observatory AppRole; it still cannot read
# kv/agentic-observatory runtime secrets.
path "auth/approle/role/agentic-observatory/role-id" {
  capabilities = ["read"]
}

path "auth/approle/role/agentic-observatory/secret-id" {
  capabilities = ["update"]
}

# Bootstrap-only authority for the central coordinator Vault Agent. The runner
# cannot read coordinator loop keys or database credentials.
path "auth/approle/role/agent-core-coordinator/role-id" {
  capabilities = ["read"]
}

path "auth/approle/role/agent-core-coordinator/secret-id" {
  capabilities = ["update"]
}

# Bootstrap-only authority for the dedicated SOC Vault Agent. The runner
# cannot read SOC runtime, database, model, or coordinator credentials.
path "auth/approle/role/soc-agent/role-id" {
  capabilities = ["read"]
}

path "auth/approle/role/soc-agent/secret-id" {
  capabilities = ["update"]
}
