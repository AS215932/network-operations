#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_FILE="${SECRETS_FILE:-${REPO_ROOT}/secrets.local.sh}"

if [ -f "${SECRETS_FILE}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${SECRETS_FILE}"
  set +a
fi

: "${VAULT_ADDR:?Set VAULT_ADDR, usually https://vault.as215932.net}"
vault token lookup >/dev/null 2>&1 || {
  echo "Set VAULT_TOKEN to a token allowed to write kv/noc-agent, or run vault login" >&2
  exit 1
}
: "${GEMINI_API_KEY:?GEMINI_API_KEY is required}"
: "${NOC_DISCORD_WEBHOOK:?NOC_DISCORD_WEBHOOK is required}"
: "${MAIL_NOC_PASSWORD:?MAIL_NOC_PASSWORD is required}"
: "${XO_TOKEN:?XO_TOKEN is required}"
: "${ICINGA_API_USER:?ICINGA_API_USER is required}"
: "${ICINGA_API_PASSWORD:?ICINGA_API_PASSWORD is required}"

vault kv put kv/noc-agent \
  gemini_api_key="${GEMINI_API_KEY}" \
  anthropic_api_key="${ANTHROPIC_API_KEY:-}" \
  openai_api_key="${OPENAI_API_KEY:-}" \
  discord_webhook_url="${NOC_DISCORD_WEBHOOK}" \
  discord_bot_token="${NOC_DISCORD_BOT_TOKEN:-}" \
  noc_control_token="${NOC_CONTROL_TOKEN:-}" \
  noc_approval_signing_secret="${NOC_APPROVAL_SIGNING_SECRET:-}" \
  mail_imap_password="${MAIL_NOC_PASSWORD}" \
  xo_token="${XO_TOKEN}" \
  icinga_api_user="${ICINGA_API_USER}" \
  icinga_api_password="${ICINGA_API_PASSWORD}"

echo "Wrote kv/noc-agent."
