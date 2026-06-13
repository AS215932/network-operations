#!/usr/bin/env bash
# cutover-remove-loop.sh — Phase G cutover: remove the Engineering Loop from
# network-operations after it has been extracted to AS215932/engineering-loop.
#
# DANGER: this deletes ~5k LOC of loop runtime, its tests, docs, skills, the
# Pi extension, the loop's pyproject/uv.lock, and the policy files. Run it
# ONLY after the new repo is live, its CI is green, and a sibling-canary run
# from the new repo has succeeded (docs/ci/engineering-loop-extraction.md).
# The Pi extension (integrations/pi/) moves WITH the extraction, so this
# removes it from network-operations; repointing the extension's install
# path/config to the engineering-loop checkout happens in the new repo (a
# post-extraction runbook step). It leaves a pointer doc at
# docs/agentic-development-loop.md. It does NOT commit — review
# `git status`/`git diff` and commit yourself, then open the cutover PR.
#
# Usage: scripts/cutover-remove-loop.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

NEW_REPO="AS215932/engineering-loop"

# Mirror of extract-engineering-loop.sh PATHS, plus the loop test suites.
REMOVE_PATHS=(
  src/hyrule_engineering_loop
  docs/agent-loops
  docs/engineering-loop
  skills
  integrations/pi
  configs/loop
  model-policy.yml
  engineering-loop-policy.yml
  pyproject.toml
  uv.lock
  tests/test_engineering_graph.py
)

cd "${REPO_ROOT}"

remove() {
  local target="$1"
  if [ ! -e "${target}" ]; then
    echo "   (already absent) ${target}"
    return
  fi
  if [ "${DRY_RUN}" -eq 1 ]; then
    echo "   would git rm -r ${target}"
  else
    git rm -r --quiet "${target}"
    echo "   removed ${target}"
  fi
}

echo ">> removing migrated loop paths"
for p in "${REMOVE_PATHS[@]}"; do remove "${p}"; done

echo ">> removing loop test suites (tests/test_phase*.py)"
shopt -s nullglob
for f in tests/test_phase*.py; do remove "${f}"; done
shopt -u nullglob

POINTER="docs/agentic-development-loop.md"
echo ">> writing pointer doc at ${POINTER}"
if [ "${DRY_RUN}" -eq 1 ]; then
  echo "   would overwrite ${POINTER} with a pointer to ${NEW_REPO}"
else
  cat > "${POINTER}" <<EOF
# Hyrule Engineering Loop — moved

The Hyrule Engineering Loop now lives in its own repository:
**[${NEW_REPO}](https://github.com/${NEW_REPO})**.

This includes the LangGraph runtime, the \`AgentBackend\`, the senior-role
skills, the task-spec / two-phase-review / memory / intake / operations-lane
machinery, the Pi \`/loop\` extension, and the design docs (\`docs/engineering-loop/\`,
\`docs/agent-loops/\`, and the runtime reference formerly at this path).

History was preserved via \`git filter-repo\` (Phase G of the v2 refactor; see
that repo and \`AS215932/network-operations#196\`). The extraction tooling that
produced it remains here at \`scripts/extract-engineering-loop.sh\` and
\`docs/ci/engineering-loop-extraction.md\` for provenance.

Nothing in network-operations imports the loop; it operates on this repo (and
the other \`hyrule-*\` repos) from the outside, opening draft PRs that humans
review and merge.
EOF
  git add "${POINTER}"
  echo "   wrote ${POINTER}"
fi

echo
echo ">> done. Review with: git status && git diff --cached --stat"
echo "   Then commit and open the cutover PR. Verify network-operations CI"
echo "   (lint/render/iac-gate) stays green — none of it depends on the loop."
