#!/bin/bash
# /etc/icinga2/scripts/notify-noc-agent.sh — POST Icinga notifications to noc-agent.
#
# Mirrors notify-discord.sh in invocation pattern but reshapes the payload
# into the JSON noc-agent's /webhook/icinga endpoint expects.
#
# Required env (set by the noc-agent-{host,service}-notification commands):
#   NOC_AGENT_URL
#   NOTIFICATION_TYPE, HOST_NAME, HOST_ADDRESS, HOST_STATE, HOST_OUTPUT, HOST_CHECK_COMMAND
# Service notifications additionally set:
#   SERVICE_NAME, SERVICE_STATE, SERVICE_OUTPUT, SERVICE_CHECK_COMMAND

set -eu

: "${NOC_AGENT_URL:?NOC_AGENT_URL not set}"
: "${NOTIFICATION_TYPE:?}"
: "${HOST_NAME:?}"

if [ -n "${SERVICE_NAME:-}" ]; then
  state="${SERVICE_STATE:-}"
  output="${SERVICE_OUTPUT:-}"
  check_command="${SERVICE_CHECK_COMMAND:-}"
else
  state="${HOST_STATE:-}"
  output="${HOST_OUTPUT:-}"
  check_command="${HOST_CHECK_COMMAND:-}"
fi

# Compose JSON. jq isn't guaranteed on mon — emit safely with python (always present).
python3 - <<'PY'
import json
import os
import sys
import urllib.request

notification_route = os.environ.get("NOTIFICATION_ROUTE") or os.environ.get("HOST_NOTIFICATION_ROUTE") or "network"
if notification_route not in {"network", "ai", "ci"}:
    notification_route = "network"

payload = {
    "host_name": os.environ.get("HOST_NAME", ""),
    "host_address": os.environ.get("HOST_ADDRESS", ""),
    "service_name": os.environ.get("SERVICE_NAME") or None,
    "check_command": os.environ.get("SERVICE_CHECK_COMMAND") or os.environ.get("HOST_CHECK_COMMAND") or None,
    "state": os.environ.get("SERVICE_STATE") or os.environ.get("HOST_STATE", ""),
    "state_type": os.environ.get("NOTIFICATION_TYPE", ""),
    "output": os.environ.get("SERVICE_OUTPUT") or os.environ.get("HOST_OUTPUT", ""),
    "tags": {
        "source": "icinga2",
        "notification_route": notification_route,
    },
}

req = urllib.request.Request(
    os.environ["NOC_AGENT_URL"],
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        sys.stdout.write(resp.read().decode())
except Exception as e:
    sys.stderr.write(f"noc-agent webhook failed: {e}\n")
    sys.exit(1)
PY
