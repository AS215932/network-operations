# Vault policy for the dedicated Knowledge Loop producer agent.
# Bound to AppRole role `knowledge-loop`; Vault Agent renders
# /etc/knowledge-loop/knowledge-loop.env from kv/data/knowledge-loop.
#
# Scope is deliberately narrow: Knowledge repo PR/issue credentials,
# Knowledge Loop OpenRouter enrichment key, and optional heartbeat credentials.
# No Engineering Loop runtime key, CI/CD PR-Agent key, fleet SSH, app runtime,
# Vault, XO, registrar, wallet, or deployment secrets belong here.

path "kv/data/knowledge-loop" {
  capabilities = ["read"]
}

path "kv/metadata/knowledge-loop" {
  capabilities = ["read"]
}
