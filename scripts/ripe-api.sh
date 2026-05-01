#!/bin/bash
# Thin wrapper around the RIPE NCC REST API (rest.db.ripe.net).
#
# Sources credentials from secrets.local.sh at the repo root (gitignored).
# Use cases: create/modify/delete `domain:`, `route6:`, `inet6num:`,
# `aut-num:` objects unattended.
#
# Usage:
#   scripts/ripe-api.sh get    <type> <key>
#   scripts/ripe-api.sh search <key>
#   scripts/ripe-api.sh create <object-file>
#   scripts/ripe-api.sh update <type> <key> <object-file>
#   scripts/ripe-api.sh delete <type> <key> [reason]
#
# Object files are RPSL (`attr: value\n`) per the RIPE database schema.
# This wrapper handles the JSON/XML envelope for you — pass plain RPSL.
#
# References:
#   https://apps.db.ripe.net/docs/REST-API/
#   https://docs.db.ripe.net/RPSL-Object-Types/

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "${REPO_ROOT}/secrets.local.sh" ]; then
  echo "secrets.local.sh missing at ${REPO_ROOT}/secrets.local.sh" >&2
  exit 1
fi
# shellcheck disable=SC1091
. "${REPO_ROOT}/secrets.local.sh"

: "${RIPE_API_AUTH:?RIPE_API_AUTH not set in secrets.local.sh}"

API="https://rest.db.ripe.net/ripe"

# Convert an RPSL file into the RIPE REST JSON envelope on stdout.
# Also prints the object type to stderr so callers can route the request.
rpsl_to_json() {
  python3 - "$1" <<'PY'
import sys, json, re
TYPES = {
    "domain","route6","inet6num","aut-num","mntner","person","role",
    "route","inetnum","organisation","key-cert","poem","poetic-form",
}
with open(sys.argv[1]) as fh:
    raw = fh.readlines()
attrs = []
for line in raw:
    line = line.rstrip("\n")
    if not line or line.startswith("#"):
        continue
    m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
    if not m:
        continue
    attrs.append({"name": m.group(1), "value": m.group(2)})
otype = next((a["name"] for a in attrs if a["name"] in TYPES), None)
if otype is None:
    sys.stderr.write("could not infer object type from first attribute\n")
    sys.exit(2)
sys.stderr.write(otype + "\n")
print(json.dumps({
    "objects": {
        "object": [
            {
                "source": {"id": "ripe"},
                "attributes": {"attribute": attrs},
            }
        ]
    }
}))
PY
}

cmd_get() {
  local type="$1" key="$2"
  curl -sS -H "Authorization: ${RIPE_API_AUTH}" -H "Accept: application/json" \
    "${API}/${type}/${key}"
}

cmd_search() {
  local key="$1"
  curl -sS -H "Authorization: ${RIPE_API_AUTH}" -H "Accept: application/json" \
    "https://rest.db.ripe.net/search.json?query-string=${key}&source=ripe"
}

cmd_create() {
  local file="$1"
  local body type
  # rpsl_to_json prints the inferred object type to stderr; capture it.
  exec 3>&1
  type="$(rpsl_to_json "$file" 2>&1 1>&3 | tail -1)"
  body="$(rpsl_to_json "$file" 2>/dev/null)"
  exec 3>&-
  curl -sS -X POST \
    -H "Authorization: ${RIPE_API_AUTH}" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    --data "$body" \
    "${API}/${type}"
}

cmd_update() {
  local type="$1" key="$2" file="$3"
  local body
  body="$(rpsl_to_json "$file" 2>/dev/null)"
  curl -sS -X PUT \
    -H "Authorization: ${RIPE_API_AUTH}" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    --data "$body" \
    "${API}/${type}/${key}"
}

cmd_delete() {
  local type="$1" key="$2" reason="${3:-cleanup}"
  curl -sS -X DELETE \
    -H "Authorization: ${RIPE_API_AUTH}" \
    -H "Accept: application/json" \
    "${API}/${type}/${key}?reason=${reason}"
}

usage() {
  sed -n '1,18p' "$0" | tail -16
  exit 2
}

case "${1:-}" in
  get)    [ $# -eq 3 ] || usage; cmd_get    "$2" "$3" ;;
  search) [ $# -eq 2 ] || usage; cmd_search "$2" ;;
  create) [ $# -eq 2 ] || usage; cmd_create "$2" ;;
  update) [ $# -eq 4 ] || usage; cmd_update "$2" "$3" "$4" ;;
  delete) [ $# -ge 3 ] || usage; cmd_delete "$2" "$3" "${4:-}" ;;
  *) usage ;;
esac
