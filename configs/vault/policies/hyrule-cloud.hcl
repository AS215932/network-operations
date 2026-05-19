# Vault policy for the hyrule-cloud service on the api VM.
# Bound to AppRole role `hyrule-cloud`; Vault Agent renders
# /opt/hyrule-cloud/.env from kv/data/hyrule-cloud.

path "kv/data/hyrule-cloud" {
  capabilities = ["read"]
}

path "kv/metadata/hyrule-cloud" {
  capabilities = ["read"]
}
