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
#   0 OK            — model chain ready and runtime/provider health OK
#   1 WARNING       — fallback ready but runtime/provider health degraded
#   2 CRITICAL      — no viable model (HTTP 503), or endpoint unreachable
#   3 UNKNOWN       — non-200/503 response, or jq parse failure

set -u

if [ "$#" -ne 2 ]; then
    echo "UNKNOWN - usage: $0 <ipv6> <port>"
    exit 3
fi
host="$1"; port="$2"

body="$(mktemp)"
err_log="$(mktemp)"
trap 'rm -f "$body" "$err_log"' EXIT

# Keep curl's stderr (TLS handshake, connection refused, timeout reason)
# so the alert body has something actionable instead of a generic
# "exit non-zero".
code="$(curl -sS --max-time 10 -o "$body" -w '%{http_code}' \
    "http://[$host]:$port/health/model" 2>"$err_log")" || {
    reason="$(tr '\n' ' ' < "$err_log" | sed 's/  */ /g')"
    echo "CRITICAL - /health/model unreachable from mon: ${reason:-curl exit non-zero}"
    exit 2
}

# Parse fields; jq -r prints "null" if a key is missing, so treat that as "?".
field() {
    val="$(jq -r "$1 // \"?\"" "$body" 2>/dev/null)" || val="?"
    [ "$val" = "null" ] && val="?"
    printf '%s' "$val"
}

status=$(field '.status')
readiness=$(field '.readiness.status')
runtime=$(field '.runtime_reliability.status')
quota=$(field '.quota_monitoring')
primary=$(field '.primary_model')
err=$(jq -r '.error // ""' "$body" 2>/dev/null || true)
# `(.missing // [])` short-circuits when .missing is null or absent, so
# `join` always receives a list — avoids the jq error
# `Cannot iterate over null` that `.missing | join(",")` raises.
missing=$(jq -r '(.missing // []) | join(",")' "$body" 2>/dev/null || true)

detail="primary=$primary status=$status readiness=$readiness runtime=$runtime quota=$quota"
[ -n "$missing" ] && detail="$detail missing=$missing"
[ -n "$err" ]     && detail="$detail err=$err"

case "$code" in
    200)
        if [ "$status" = "ok" ] && [ "$readiness" = "ok" ] && [ "$runtime" = "ok" ]; then
            echo "OK - $detail"; exit 0
        fi
        echo "WARNING - $detail"; exit 1
        ;;
    503) echo "CRITICAL - $detail"; exit 2 ;;
    *)   echo "UNKNOWN - http=$code $detail"; exit 3 ;;
esac
