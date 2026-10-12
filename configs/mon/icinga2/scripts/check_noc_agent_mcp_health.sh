#!/bin/sh
# check_noc_agent_mcp_health.sh — Icinga2 check executed on mon.
#
# Curls noc-agent's /health/mcp and surfaces the JSON body's per-source
# state. The stock check_http output only reports HTTP 503 + regex failure;
# this wrapper identifies whether hyrule-mcp or xo-mcp is degraded and why.
#
# Usage: check_noc_agent_mcp_health.sh <ipv6> <port>

set -u

if [ "$#" -ne 2 ]; then
    echo "UNKNOWN - usage: $0 <ipv6> <port>"
    exit 3
fi
host="$1"; port="$2"

body="$(mktemp)"
err_log="$(mktemp)"
trap 'rm -f "$body" "$err_log"' EXIT

code="$(curl -sS --max-time 15 -o "$body" -w '%{http_code}' \
    "http://[$host]:$port/health/mcp" 2>"$err_log")" || {
    reason="$(tr '\n' ' ' < "$err_log" | sed 's/  */ /g')"
    echo "CRITICAL - /health/mcp unreachable from mon: ${reason:-curl exit non-zero}"
    exit 2
}

field() {
    val="$(jq -r "$1 // \"?\"" "$body" 2>/dev/null)" || val="?"
    [ "$val" = "null" ] && val="?"
    printf '%s' "$val"
}

source_detail() {
    src="$1"
    ready="$(field ".sources.$src.ready")"
    tools="$(field ".sources.$src.tool_count")"
    error="$(field ".sources.$src.error")"
    printf '%s_ready=%s %s_tools=%s %s_error=%s' "$src" "$ready" "$src" "$tools" "$src" "$error"
}

status="$(field '.status')"
hyrule="$(source_detail hyrule)"
xo="$(source_detail xo)"
detail="status=$status $hyrule $xo"

case "$code" in
    200)
        if [ "$status" = "ok" ]; then
            echo "OK - $detail"
            exit 0
        fi
        echo "CRITICAL - http=200 but $detail"
        exit 2
        ;;
    503)
        echo "CRITICAL - $detail"
        exit 2
        ;;
    *)
        echo "UNKNOWN - http=$code $detail"
        exit 3
        ;;
esac
