#!/usr/bin/env bash
# extract-engineering-loop.sh — Phase G of the Engineering Loop v2 refactor.
#
# Produces a history-preserving export of the Engineering Loop subtree from
# network-operations into a standalone working copy, ready to push to
# AS215932/engineering-loop. The roadmap calls this "git subtree split", but
# subtree split only handles a single directory prefix; the loop spans many
# top-level paths (src/, a subset of tests/, several docs/ subtrees, skills/,
# integrations/pi/, and a few root files), so this uses git-filter-repo, the
# modern tool for multi-path history extraction. Every commit that touched a
# kept path is preserved (AC1: history for migrated files survives).
#
# This script is non-destructive to the source: it operates on a fresh clone
# in a temp dir, never on your working checkout. It does NOT create the GitHub
# repo or push — those are the operator's irreversible steps, see
# docs/ci/engineering-loop-extraction.md.
#
# Usage:
#   scripts/extract-engineering-loop.sh [--source <path-or-url>] \
#       [--ref <git-ref>] --target <dir> [--verify]
#
#   --source  network-operations checkout or clone URL (default: this repo root)
#   --ref     ref holding the full loop code (default: the current HEAD)
#   --target  output directory for the extracted repo (required; must not exist)
#   --verify  run pytest + mypy --strict in the extracted tree afterwards
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SCAFFOLD_DIR="${REPO_ROOT}/integrations/engineering-loop"

SOURCE="${REPO_ROOT}"
REF="HEAD"
TARGET=""
VERIFY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --verify) VERIFY=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "${TARGET}" ]; then
  echo "error: --target <dir> is required" >&2
  exit 2
fi
if [ -e "${TARGET}" ]; then
  echo "error: target already exists: ${TARGET}" >&2
  exit 2
fi

# Paths to migrate, with history. Keep this list in step with the roadmap
# (docs/engineering-loop/v2-roadmap.md, section G) and the cutover script.
PATHS=(
  src/hyrule_engineering_loop
  docs/agent-loops
  docs/agentic-development-loop.md
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
# The loop test suites are tests/test_phase*.py; everything else under tests/
# (iac/, goss/) belongs to network-operations and stays behind.
PATH_GLOBS=(
  'tests/test_phase*.py'
)

filter_repo() {
  if command -v git-filter-repo >/dev/null 2>&1; then
    git filter-repo "$@"
  elif command -v uvx >/dev/null 2>&1; then
    uvx git-filter-repo "$@"
  else
    echo "error: git-filter-repo not found (install via 'uv tool install git-filter-repo' or pipx)" >&2
    exit 3
  fi
}

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

echo ">> cloning ${SOURCE}@${REF} into a scratch clone"
git clone --no-local --quiet "${SOURCE}" "${WORKDIR}/clone"
git -C "${WORKDIR}/clone" checkout --quiet --detach "${REF}"

echo ">> filtering to the engineering-loop subtree (history preserved)"
FILTER_ARGS=()
for p in "${PATHS[@]}"; do FILTER_ARGS+=(--path "${p}"); done
for g in "${PATH_GLOBS[@]}"; do FILTER_ARGS+=(--path-glob "${g}"); done
( cd "${WORKDIR}/clone" && filter_repo "${FILTER_ARGS[@]}" --force )

echo ">> materializing new-repo scaffolding"
mkdir -p "${WORKDIR}/clone/.github/workflows"
cp "${SCAFFOLD_DIR}/ci.yml" "${WORKDIR}/clone/.github/workflows/ci.yml"
cp "${SCAFFOLD_DIR}/README.md" "${WORKDIR}/clone/README.md"
cp "${SCAFFOLD_DIR}/gitignore" "${WORKDIR}/clone/.gitignore"
( cd "${WORKDIR}/clone" \
  && git add .github/workflows/ci.yml README.md .gitignore \
  && git -c user.name='Engineering Loop' -c user.email='loop@as215932.net' \
       commit --quiet -m "Add standalone repo CI, README, and gitignore" )

mv "${WORKDIR}/clone" "${TARGET}"
echo ">> extracted repo ready at: ${TARGET}"
echo "   commits: $(git -C "${TARGET}" rev-list --count HEAD)"

if [ "${VERIFY}" -eq 1 ]; then
  echo ">> verifying extracted tree (pytest + mypy --strict)"
  ( cd "${TARGET}" \
    && uv run --group dev python -m pytest -q \
    && uv run --group dev mypy --strict src )
  echo ">> verification passed"
fi

cat <<EOF

Next (operator, irreversible — see docs/ci/engineering-loop-extraction.md):
  gh repo create AS215932/engineering-loop --private --source ${TARGET} --remote origin
  git -C ${TARGET} push -u origin HEAD:main
Then add the repo to the 'public-pr' runner group and protect main on the
ci/ruff/mypy checks. Only after the new repo's CI is green and a sibling-canary
run succeeds should the network-operations cutover (scripts/cutover-remove-loop.sh)
be merged.
EOF
