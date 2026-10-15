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
quota=$(field '.quota_monitoring')
primary=$(field '.primary_model')
err=$(jq -r '.error // ""' "$body" 2>/dev/null || true)
# `(.missing // [])` short-circuits when .missing is null or absent, so
# `join` always receives a list — avoids the jq error
# `Cannot iterate over null` that `.missing | join(",")` raises.
missing=$(jq -r '(.missing // []) | join(",")' "$body" 2>/dev/null || true)
missing_creds=$(jq -r '(.missing_credentials // []) | join(",")' "$body" 2>/dev/null || true)
unsupported=$(jq -r '(.unsupported_models // []) | join(",")' "$body" 2>/dev/null || true)
last_failure=$(jq -r 'if .last_failure_category then ((.last_failure_category | tostring) + ":" + ((.last_failure_model // "?") | tostring)) else "" end' "$body" 2>/dev/null || true)

# OpenRouter quota/credit probe details are nested under provider_monitoring
# (and duplicated as quota for backward compatibility). Include the safe
# public message and numeric remaining values so pages are actionable without
# requiring a follow-up curl+jq session from mon/noc.
or_status=$(field '.provider_monitoring.providers.openrouter.status // .quota.providers.openrouter.status')
or_key_status=$(field '.provider_monitoring.providers.openrouter.key.status // .quota.providers.openrouter.key.status')
or_key_msg=$(field '.provider_monitoring.providers.openrouter.key.message // .quota.providers.openrouter.key.message')
or_key_remaining=$(field '.provider_monitoring.providers.openrouter.key.limit_remaining // .quota.providers.openrouter.key.limit_remaining')
or_key_limit=$(field '.provider_monitoring.providers.openrouter.key.limit // .quota.providers.openrouter.key.limit')
or_key_usage=$(field '.provider_monitoring.providers.openrouter.key.usage // .quota.providers.openrouter.key.usage')
or_key_daily=$(field '.provider_monitoring.providers.openrouter.key.usage_daily // .quota.providers.openrouter.key.usage_daily')
or_key_monthly=$(field '.provider_monitoring.providers.openrouter.key.usage_monthly // .quota.providers.openrouter.key.usage_monthly')
or_account_status=$(field '.provider_monitoring.providers.openrouter.account.status // .quota.providers.openrouter.account.status')
or_account_msg=$(field '.provider_monitoring.providers.openrouter.account.message // .quota.providers.openrouter.account.message')
or_account_total=$(field '.provider_monitoring.providers.openrouter.account.total_credits // .quota.providers.openrouter.account.total_credits')
or_account_usage=$(field '.provider_monitoring.providers.openrouter.account.total_usage // .quota.providers.openrouter.account.total_usage')
or_account_remaining=$(field '.provider_monitoring.providers.openrouter.account.remaining // .quota.providers.openrouter.account.remaining')

# Prefer account-wide remaining credit from the management API key when it is
# available. Fall back to the per-key limit_remaining value; that is a key cap,
# not necessarily the whole account balance.
quota_left="$or_account_remaining"
quota_left_source="account"
if [ "$quota_left" = "?" ]; then
    quota_left="$or_key_remaining"
    quota_left_source="key_limit"
fi

detail="primary=$primary status=$status quota=$quota"
[ -n "$missing" ] && detail="$detail missing=$missing"
[ -n "$missing_creds" ] && detail="$detail missing_creds=$missing_creds"
[ -n "$unsupported" ] && detail="$detail unsupported=$unsupported"
[ -n "$last_failure" ] && detail="$detail last_failure=$last_failure"
if [ "$or_status" != "?" ]; then
    detail="$detail openrouter=$or_status key=$or_key_status"
    [ "$quota_left" != "?" ] && detail="$detail openrouter_quota_left_usd=$quota_left source=$quota_left_source"
    [ "$or_key_remaining" != "?" ] && detail="$detail key_remaining_usd=$or_key_remaining"
    [ "$or_key_limit" != "?" ] && detail="$detail key_limit_usd=$or_key_limit"
    [ "$or_key_usage" != "?" ] && detail="$detail key_usage_usd=$or_key_usage"
    [ "$or_key_daily" != "?" ] && detail="$detail key_daily_usd=$or_key_daily"
    [ "$or_key_monthly" != "?" ] && detail="$detail key_monthly_usd=$or_key_monthly"
    [ "$or_key_msg" != "?" ] && detail="$detail key_msg=\"$or_key_msg\""
    [ "$or_account_status" != "?" ] && detail="$detail account=$or_account_status"
    [ "$or_account_remaining" != "?" ] && detail="$detail account_remaining_usd=$or_account_remaining"
    [ "$or_account_total" != "?" ] && detail="$detail account_total_usd=$or_account_total"
    [ "$or_account_usage" != "?" ] && detail="$detail account_usage_usd=$or_account_usage"
    [ "$or_account_msg" != "?" ] && detail="$detail account_msg=\"$or_account_msg\""
fi
[ -n "$err" ]     && detail="$detail err=$err"

perfdata=""
is_number() {
    printf '%s' "$1" | grep -Eq '^-?[0-9]+([.][0-9]+)?$'
}
add_perf() {
    label="$1"; value="$2"
    is_number "$value" || return 0
    [ -n "$perfdata" ] && perfdata="$perfdata "
    perfdata="$perfdata'$label'=$value"
}
add_perf openrouter_quota_left "$quota_left"
add_perf openrouter_account_remaining "$or_account_remaining"
add_perf openrouter_account_total "$or_account_total"
add_perf openrouter_account_usage "$or_account_usage"
add_perf openrouter_key_remaining "$or_key_remaining"
add_perf openrouter_key_limit "$or_key_limit"
add_perf openrouter_key_usage "$or_key_usage"
add_perf openrouter_key_daily "$or_key_daily"
add_perf openrouter_key_monthly "$or_key_monthly"
[ -n "$perfdata" ] && detail="$detail | $perfdata"

case "$code" in
    200) echo "OK - $detail"; exit 0 ;;
    503) echo "CRITICAL - $detail"; exit 2 ;;
    *)   echo "UNKNOWN - http=$code $detail"; exit 3 ;;
esac
