# Vault policy for the agent-core trace collector on loop.
# Bound to AppRole role `agent-core-collector`; Vault Agent renders
# /etc/agent-core-collector/collector.env from kv/data/agent-core-collector.
#
# Scope is deliberately narrow: only the collector-local Postgres password.
# No Engineering Loop, Knowledge, NOC, CI, fleet SSH, or provider credentials
# belong here.

path "kv/data/agent-core-collector" {
  capabilities = ["read"]
}

path "kv/metadata/agent-core-collector" {
  capabilities = ["read"]
}
