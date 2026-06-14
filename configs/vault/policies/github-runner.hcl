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
