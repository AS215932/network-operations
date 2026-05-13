#!/bin/sh
# check_jool_success_delta.sh — Icinga2 check executed via by_ssh on rtr.
#
# Tracks JSTAT_SUCCESS over time. If Jool is responding but no successful
# translations have happened since the last invocation, alert WARNING.
# A flat counter is the strongest signal that "NAT64 is plumbed but no
# overlay traffic is reaching it" — that's how the cr1-de1 NDP outage
# would have shown up if we'd had this check during it.
#
# State stored under /var/lib/icinga2/jool-success-prev. Owned by the
# nagios user (the by_ssh login).
#
# Exit codes:
#   0 OK
#   1 WARNING — no SUCCESS deltas since previous run
#   2 CRITICAL — jool not responsive (binary missing, instance not found)
#   3 UNKNOWN — first run / state file just created

set -eu

INSTANCE="${JOOL_INSTANCE:-nat64}"
STATE_DIR="${JOOL_STATE_DIR:-/var/lib/icinga2}"
STATE_FILE="${STATE_DIR}/jool-${INSTANCE}-success-prev"

if ! command -v jool >/dev/null 2>&1; then
    echo "CRITICAL: jool binary not on PATH"
    exit 2
fi

raw=$(jool -i "${INSTANCE}" stats display 2>&1) || {
    echo "CRITICAL: jool stats failed: ${raw}"
    exit 2
}

current=$(printf '%s\n' "${raw}" | awk -F': ' '$1 == "JSTAT_SUCCESS" { print $2 }')
if [ -z "${current}" ]; then
    echo "CRITICAL: JSTAT_SUCCESS missing from jool output"
    exit 2
fi

if [ ! -f "${STATE_FILE}" ]; then
    mkdir -p "${STATE_DIR}"
    printf '%s\n' "${current}" > "${STATE_FILE}"
    echo "UNKNOWN: first run, baseline JSTAT_SUCCESS=${current}"
    exit 3
fi

prev=$(cat "${STATE_FILE}")
delta=$((current - prev))
printf '%s\n' "${current}" > "${STATE_FILE}"

if [ "${delta}" -le 0 ]; then
    pool6_mismatch=$(printf '%s\n' "${raw}" | awk -F': ' '$1 == "JSTAT_POOL6_MISMATCH" { print $2 }')
    echo "WARNING: JSTAT_SUCCESS=${current} unchanged since previous run (delta=${delta}) — POOL6_MISMATCH=${pool6_mismatch:-?}"
    exit 1
fi

echo "OK: JSTAT_SUCCESS=${current} (delta=+${delta} since last run)"
exit 0
