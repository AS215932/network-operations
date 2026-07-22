# Bound only to AppRole `agent-core-coordinator`.
path "kv/data/agent-core-coordinator" {
  capabilities = ["read"]
}

path "kv/metadata/agent-core-coordinator" {
  capabilities = ["read"]
}
