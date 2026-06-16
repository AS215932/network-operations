#!/usr/bin/env bash
# VPS launch-proof smoke — exercises the hyrule-cloud launch-proof contract.
#
# Automates the no-payment-required checks and the status-contract assertions
# from docs/runbooks/vps-launch-proof-smoke.md. The paid create + provisioning
# leg is operator-driven (x402 test wallet); pass --vm-id <id> for an already
# created VM to assert its launch-proof status fields, DNS AAAA, and SSH.
#
# Usage:
#   scripts/smoke/vps-launch-proof.sh --base https://api.hyrule.host [--vm-id VM] [--quote-file q.json]
#
# Requires: curl, jq. Optional: dig, ssh (for steps 5–6).
set -euo pipefail

BASE=""
VM_ID=""
QUOTE_FILE=""

die() { printf 'launch-proof-smoke: %s\n' "$*" >&2; exit 1; }
note() { printf '\n=== %s ===\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE="${2:-}"; shift 2 ;;
    --vm-id) VM_ID="${2:-}"; shift 2 ;;
    --quote-file) QUOTE_FILE="${2:-}"; shift 2 ;;
    *) die "unknown arg: $1" ;;
  esac
done

[ -n "$BASE" ] || die "--base <cloud-api-base> is required"
command -v curl >/dev/null || die "curl is required"
command -v jq >/dev/null || die "jq is required"
BASE="${BASE%/}"

# 1. Quote -----------------------------------------------------------------
note "1. quote (POST /v1/vm/quote)"
quote_payload='{"size":"s1","region":"nl1","os":"debian-13"}'
[ -n "$QUOTE_FILE" ] && quote_payload="$(cat "$QUOTE_FILE")"
quote_resp="$(curl -fsS -X POST "$BASE/v1/vm/quote" -H 'content-type: application/json' -d "$quote_payload")" \
  || die "quote request failed (check --quote-file matches the current VMQuoteRequest schema)"
quote_id="$(printf '%s' "$quote_resp" | jq -r '.quote_id // empty')"
[ -n "$quote_id" ] || die "no quote_id in quote response: $quote_resp"
printf 'quote_id=%s status=%s\n' "$quote_id" "$(printf '%s' "$quote_resp" | jq -r '.status // "?"')"

# 2. Unpaid create must be x402-gated -------------------------------------
note "2. unpaid create returns 402"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/vm/create" \
  -H 'content-type: application/json' -d "{\"quote_id\":\"$quote_id\"}")"
[ "$code" = "402" ] || die "expected HTTP 402 for unpaid create, got $code"
printf 'unpaid create -> HTTP 402 (payment enforced) OK\n'

# 3–4. Paid create + provisioning are operator-driven (x402 test wallet).
note "3-4. paid create + provisioning"
printf 'operator step: pay quote %s via the test wallet, POST /v1/vm/create, then re-run with --vm-id <id>\n' "$quote_id"

# 5–6. Status contract + DNS + SSH (when a VM id is supplied) --------------
if [ -n "$VM_ID" ]; then
  note "status contract (GET /v1/vm/$VM_ID/status)"
  status="$(curl -fsS "$BASE/v1/vm/$VM_ID/status")" || die "status request failed for $VM_ID"
  printf '%s\n' "$status" | jq '{status, payment_status, dns_aaaa_verified, ssh_smoke_status, rollback_available, fqdn, ipv6, customer_message}'
  for field in status payment_status dns_aaaa_verified ssh_smoke_status rollback_available; do
    printf '%s' "$status" | jq -e "has(\"$field\")" >/dev/null || die "status missing launch-proof field: $field"
  done
  printf 'launch-proof status fields present OK\n'

  fqdn="$(printf '%s' "$status" | jq -r '.fqdn // empty')"
  ipv6="$(printf '%s' "$status" | jq -r '.ipv6 // empty')"
  if [ -n "$fqdn" ] && command -v dig >/dev/null; then
    note "5. DNS AAAA ($fqdn)"
    dig +short AAAA "$fqdn" || true
  fi
  if [ -n "$ipv6" ] && command -v ssh >/dev/null; then
    note "6. SSH reachability ($ipv6)"
    ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "root@$ipv6" true \
      && printf 'SSH connect OK\n' || printf 'SSH not reachable (check ssh_smoke_status above)\n'
  fi
else
  printf '\n(no --vm-id given; skipped status/DNS/SSH. Re-run with --vm-id after a paid create.)\n'
fi

note "done"
