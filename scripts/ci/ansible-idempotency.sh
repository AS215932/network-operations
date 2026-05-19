#!/usr/bin/env bash
# Safe idempotency gate for CI. This does not mutate production hosts: it runs
# render/validate paths twice and asserts the generated artifact tree is stable.
# Real apply-twice Molecule/Containerlab scenarios can plug in here as roles
# gain safe test targets.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

export ANSIBLE_LOCAL_TEMP="${ANSIBLE_LOCAL_TEMP:-/tmp/ansible-local}"
export ANSIBLE_REMOTE_TEMP="${ANSIBLE_REMOTE_TEMP:-/tmp/ansible-remote}"
mkdir -p "$ANSIBLE_LOCAL_TEMP" "$ANSIBLE_REMOTE_TEMP"

scripts/ci/render-all.sh
git diff --exit-code ansible/generated/

scripts/ci/render-all.sh
git diff --exit-code ansible/generated/
