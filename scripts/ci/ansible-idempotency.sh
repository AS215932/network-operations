#!/usr/bin/env bash
# Safe idempotency gate for CI. This does not mutate production hosts: it runs
# render/validate paths twice and asserts the generated artifact tree is stable.
# Real apply-twice Molecule/Containerlab scenarios can plug in here as roles
# gain safe test targets.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

ci_tmp_root="${RUNNER_TEMP:-}"
if [[ -z "$ci_tmp_root" ]]; then
  ci_tmp_root="${GITHUB_WORKSPACE:-$repo_root}/.tmp"
fi

export ANSIBLE_LOCAL_TEMP="${ANSIBLE_LOCAL_TEMP:-$ci_tmp_root/ansible-local}"
export ANSIBLE_REMOTE_TEMP="${ANSIBLE_REMOTE_TEMP:-$ci_tmp_root/ansible-remote}"
mkdir -p "$ANSIBLE_LOCAL_TEMP" "$ANSIBLE_REMOTE_TEMP"

scripts/ci/render-all.sh
git diff --exit-code ansible/generated/

scripts/ci/render-all.sh
git diff --exit-code ansible/generated/
