# Vault policy for the dedicated Engineering Loop VM.
# Bound to AppRole role `engineering-loop`; Vault Agent renders
# /opt/engineering-loop/.env from kv/data/engineering-loop.
#
# Scope is deliberately narrow: GitHub issue/PR credentials, model-provider
# keys, Discord/Icinga notification credentials. No fleet SSH, app runtime,
# Vault, XO, registrar, or deployment secrets belong here.

path "kv/data/engineering-loop" {
  capabilities = ["read"]
}

path "kv/metadata/engineering-loop" {
  capabilities = ["read"]
}
