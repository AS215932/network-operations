#!/bin/sh
# check_noc_agent_model_health.sh — Icinga2 check executed on mon.
#
# Curls noc-agent's /health/model and surfaces the JSON body's reason
# fields in the plugin output line. Replaces the generic `check_http`
# command, which only reports the HTTP status code and throws away the
# JSON explaining *why* the model is unavailable (failing model name,
# quota state, missing creds).
#
# Endpoint shape is fixed by hyrule-noc-agent/app/main.py:647-662 —
# {status, missing[], quota_monitoring, quota, primary_model,
# fallback_models}. jq extraction matches that contract.
#
# Usage: check_noc_agent_model_health.sh <ipv6> <port>
#
# Exit codes:
#   0 OK            — HTTP 200, status=ok
#   2 CRITICAL      — HTTP 503 (or unreachable)
#   3 UNKNOWN       — non-200/503 response, or jq parse failure

set -u

if [ "$#" -ne 2 ]; then
    echo "UNKNOWN - usage: $0 <ipv6> <port>"
    exit 3
fi
host="$1"; port="$2"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT

code="$(curl -sS --max-time 10 -o "$body" -w '%{http_code}' \
    "http://[$host]:$port/health/model" 2>/dev/null)" || {
    echo "CRITICAL - /health/model unreachable from mon (curl exit non-zero)"
    exit 2
}

# Parse fields; jq -r prints "null" if a key is missing, so treat that as "?".
field() {
    val="$(jq -r "$1 // \"?\"" "$body" 2>/dev/null)" || val="?"
    [ "$val" = "null" ] && val="?"
    printf '%s' "$val"
}

status=$(field '.status')
quota=$(field '.quota_monitoring')
primary=$(field '.primary_model')
err=$(jq -r '.error // ""' "$body" 2>/dev/null || true)
missing=$(jq -r '.missing | join(",") // ""' "$body" 2>/dev/null || true)
[ "$missing" = "null" ] && missing=""

detail="primary=$primary status=$status quota=$quota"
[ -n "$missing" ] && detail="$detail missing=$missing"
[ -n "$err" ]     && detail="$detail err=$err"

case "$code" in
    200) echo "OK - $detail"; exit 0 ;;
    503) echo "CRITICAL - $detail"; exit 2 ;;
    *)   echo "UNKNOWN - http=$code $detail"; exit 3 ;;
esac
