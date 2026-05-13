#!/usr/bin/env bash
# scripts/ci/init-labels.sh
#
# Idempotent label creation for the CI lane. Run once after bootstrap to
# ensure the labels referenced by ai-review.yml and auto-merge.yml exist.
#
# Usage:
#   gh auth status                    # confirm logged in
#   scripts/ci/init-labels.sh
#
# Safe to re-run: `gh label create --force` updates color/description on
# existing labels rather than failing.

set -euo pipefail

REPO="${REPO:-AS215932/network-operations}"

declare -A labels=(
  [safe-class]="0e8a16:Auto-merge candidate (generated/, docs, or *.md only)"
  [ai-reviewed]="cccccc:AI review has been posted on this PR"
  [needs-human-review]="d4c5f9:AI review classified this as needs-review"
  [risky]="b60205:AI review classified this as risky (BGP/NAT64/firewall/Vault)"
  [routine]="0052cc:Recurring operational task or low-risk infra change"
)

for name in "${!labels[@]}"; do
  spec="${labels[$name]}"
  color="${spec%%:*}"
  desc="${spec#*:}"
  echo "ensuring label: $name ($color)"
  gh label create "$name" \
    --repo "$REPO" \
    --color "$color" \
    --description "$desc" \
    --force
done

echo "done."
