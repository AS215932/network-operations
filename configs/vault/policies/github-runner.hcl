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
