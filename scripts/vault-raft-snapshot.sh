#!/usr/bin/env bash
set -euo pipefail

: "${VAULT_ADDR:?Set VAULT_ADDR, usually https://vault.as215932.net}"
: "${VAULT_TOKEN:?Set VAULT_TOKEN to a token allowed to run raft snapshots}"
: "${VAULT_SNAPSHOT_AGE_RECIPIENT:?Set VAULT_SNAPSHOT_AGE_RECIPIENT to an age public recipient}"

command -v vault >/dev/null 2>&1 || {
  echo "missing required command: vault" >&2
  exit 1
}

command -v age >/dev/null 2>&1 || {
  echo "missing required command: age" >&2
  exit 1
}

umask 077
SNAPSHOT_DIR="${VAULT_SNAPSHOT_DIR:-./vault-snapshots}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PLAIN="$(mktemp "${TMPDIR:-/tmp}/vault-raft-${STAMP}.XXXXXX.snap")"
OUT="${SNAPSHOT_DIR}/vault-raft-${STAMP}.snap.age"

mkdir -p "${SNAPSHOT_DIR}"
vault operator raft snapshot save "${PLAIN}"
age -r "${VAULT_SNAPSHOT_AGE_RECIPIENT}" -o "${OUT}" "${PLAIN}"
rm -f "${PLAIN}"

echo "Wrote encrypted Vault raft snapshot: ${OUT}"
