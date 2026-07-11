#!/usr/bin/env bash
# scripts/ci/render-all.sh
#
# Render every Ansible playbook in --tags validate --connection=local mode,
# without applying. Used by .github/workflows/render-check.yml to assert
# that ansible/generated/<host>/* matches what the role would produce.
#
# Usage:
#   scripts/ci/render-all.sh           # render all playbooks
#   scripts/ci/render-all.sh firewall  # render just one playbook
#
# Exit codes:
#   0  every requested playbook rendered cleanly
#   1  one or more playbooks failed to render
#
# Secrets:
#   This script does NOT need any real secret values — it only renders
#   templates. Anything sourced from `lookup('env', ...)` falls through to an
#   empty default and the template handles it. If a render starts requiring
#   a real secret to validate, that's a bug in the template.

set -euo pipefail

cd "$(dirname "$0")/../../ansible"

# Stub out env vars that the playbooks read at render time. Real values are
# only needed on apply.
export ANSIBLE_FORCE_COLOR=true
export DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-https://discord.com/api/webhooks/0/render-stub}"
export ICINGA_API_USER="${ICINGA_API_USER:-render-stub}"
export ICINGA_API_PASSWORD="${ICINGA_API_PASSWORD:-render-stub}"
export GEMINI_API_KEY="${GEMINI_API_KEY:-render-stub}"
export NOC_DISCORD_WEBHOOK="${NOC_DISCORD_WEBHOOK:-https://discord.com/api/webhooks/0/render-stub}"
export XO_TOKEN="${XO_TOKEN:-render-stub}"
export MAIL_NOC_PASSWORD="${MAIL_NOC_PASSWORD:-render-stub}"
export NOC_MCP_KEY_PATH="${NOC_MCP_KEY_PATH:-/dev/null}"
export VAULT_NOC_AGENT_ROLE_ID="${VAULT_NOC_AGENT_ROLE_ID:-render-stub}"
export VAULT_NOC_AGENT_SECRET_ID="${VAULT_NOC_AGENT_SECRET_ID:-render-stub}"

playbooks=()
if [ "$#" -gt 0 ]; then
  for p in "$@"; do
    playbooks+=("playbooks/${p}.yml")
  done
else
  # Default set — playbooks that have a render-only `--tags validate` path.
  # Skip ones whose role is apply-only (vault, knot, networkd_resolved, etc.)
  # to keep the workflow tight.
  for p in firewall monitoring logs icinga2 prometheus alertmanager ci; do
    [ -f "playbooks/${p}.yml" ] || continue
    playbooks+=("playbooks/${p}.yml")
  done
fi

fail=0
for pb in "${playbooks[@]}"; do
  echo "::group::render ${pb}"
  # NOTE: render with `--tags validate` only — never add `apply` to a
  # --skip-tags here. The controller-render tasks that write
  # ansible/generated/<host>/* are tagged `[validate, diff, apply]` (they
  # re-render the review artifact during a real apply too), and because
  # --skip-tags wins over --tags in Ansible, skipping `apply` would silently
  # drop those render tasks, so this script would produce nothing and the
  # render check would pass trivially (issue #109). The live-mutation tasks are
  # all gated on `*_apply | default(false)` and tagged `[apply]`-only, so they
  # are not selected by `--tags validate` — render stays side-effect-free.
  # This matches the documented manual workflow in CLAUDE.md.
  if ! ansible-playbook "${pb}" \
       --tags validate \
       --connection=local; then
    echo "::error::render failed: ${pb}"
    fail=1
  fi
  echo "::endgroup::"
done

exit "${fail}"
