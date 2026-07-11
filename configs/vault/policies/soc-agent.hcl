# Bound only to the dedicated SOC VM AppRole.
path "kv/data/soc-agent" {
  capabilities = ["read"]
}

path "kv/metadata/soc-agent" {
  capabilities = ["read"]
}
