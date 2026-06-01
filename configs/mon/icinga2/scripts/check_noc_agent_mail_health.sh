#!/bin/sh
# check_noc_agent_mail_health.sh — Icinga2 check executed on mon.
#
# Curls noc-agent's /health/mail and surfaces the JSON body's reason fields
# in the plugin output line. Replaces the generic `check_http` command, which
# only reports the HTTP status code and throws away the JSON explaining *why*
# the mailbox poll is unhealthy (IMAP login failure, TLS error, missing creds,
# select failure).
#
# Endpoint shape is fixed by hyrule-noc-agent/app/main.py:797-807 +
# app/mail.py:240-264:
#   200 OK        -> {status:"ok", host, user, mailbox, message_count}
#   503 degraded  -> {status:"degraded", error:"<safe reason>"}
# jq extraction matches that contract.
#
# Usage: check_noc_agent_mail_health.sh <ipv6> <port>
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

# Fail diagnosably if the plugin's own deps are missing, rather than emitting a
# confusing parse/curl error.
for dep in curl jq; do
    command -v "$dep" >/dev/null 2>&1 || { echo "UNKNOWN - required tool '$dep' not found on mon"; exit 3; }
done

body="$(mktemp)"
err_log="$(mktemp)"
trap 'rm -f "$body" "$err_log"' EXIT

# Keep curl's stderr (TLS handshake, connection refused, timeout reason) so the
# alert body has something actionable instead of a generic "exit non-zero".
code="$(curl -sS --max-time 15 -o "$body" -w '%{http_code}' \
    "http://[$host]:$port/health/mail" 2>"$err_log")" || {
    reason="$(tr '\n' ' ' < "$err_log" | sed 's/  */ /g')"
    echo "CRITICAL - /health/mail unreachable from mon: ${reason:-curl exit non-zero}"
    exit 2
}

# Parse fields; jq -r prints "null" if a key is missing, so treat that as "?".
field() {
    val="$(jq -r "$1 // \"?\"" "$body" 2>/dev/null)" || val="?"
    [ "$val" = "null" ] && val="?"
    printf '%s' "$val"
}

status=$(field '.status')
mbox=$(field '.mailbox')
count=$(field '.message_count')
# Go through field() too, so a malformed body fails the same way for every key
# ("?"). On the happy path .error is absent → "?" → omitted from the detail.
err=$(field '.error')

detail="status=$status mailbox=$mbox count=$count"
[ -n "$err" ] && [ "$err" != "?" ] && detail="$detail err=$err"

case "$code" in
    200) echo "OK - $detail"; exit 0 ;;
    503) echo "CRITICAL - $detail"; exit 2 ;;
    *)   echo "UNKNOWN - http=$code $detail"; exit 3 ;;
esac
