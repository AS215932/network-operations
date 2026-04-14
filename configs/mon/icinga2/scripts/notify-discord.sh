#!/bin/bash
# /etc/icinga2/scripts/notify-discord.sh — Post Icinga notification to Discord webhook.
#
# Invoked by the discord-host-notification and discord-service-notification
# NotificationCommands. Reads context from environment variables set by Icinga.
#
# Required env:
#   DISCORD_WEBHOOK_URL
#   NOTIFICATION_TYPE, HOST_NAME, HOST_DISPLAYNAME, HOST_STATE, HOST_OUTPUT
# Service notifications additionally set:
#   SERVICE_NAME, SERVICE_DISPLAYNAME, SERVICE_STATE, SERVICE_OUTPUT

set -eu

: "${DISCORD_WEBHOOK_URL:?DISCORD_WEBHOOK_URL not set}"
: "${NOTIFICATION_TYPE:?}"
: "${HOST_NAME:?}"

ICINGA_URL="${ICINGA_URL:-https://mon.servify.network/icingaweb2}"

if [ -n "${SERVICE_NAME:-}" ]; then
  kind="service"
  state="${SERVICE_STATE}"
  title="${NOTIFICATION_TYPE}: ${SERVICE_DISPLAYNAME:-$SERVICE_NAME} on ${HOST_DISPLAYNAME:-$HOST_NAME}"
  output="${SERVICE_OUTPUT:-}"
  url="${ICINGA_URL}/monitoring/service/show?host=${HOST_NAME}&service=${SERVICE_NAME}"
else
  kind="host"
  state="${HOST_STATE}"
  title="${NOTIFICATION_TYPE}: ${HOST_DISPLAYNAME:-$HOST_NAME} is ${HOST_STATE}"
  output="${HOST_OUTPUT:-}"
  url="${ICINGA_URL}/monitoring/host/show?host=${HOST_NAME}"
fi

# Discord embed color (decimal). Green=OK/Up, Yellow=Warning, Red=Critical/Down,
# Purple=Unknown, Grey for acknowledgements/flapping/custom.
case "${NOTIFICATION_TYPE}" in
  ACKNOWLEDGEMENT|FLAPPINGSTART|FLAPPINGEND|DOWNTIMESTART|DOWNTIMEEND|DOWNTIMEREMOVED|CUSTOM)
    color=9807270
    ;;
  *)
    case "${state}" in
      OK|UP)        color=3066993 ;;
      WARNING)      color=15844367 ;;
      CRITICAL|DOWN) color=15158332 ;;
      UNKNOWN)      color=10181046 ;;
      *)            color=9807270 ;;
    esac
    ;;
esac

# Truncate output to fit Discord's 1024-char field limit (leave headroom).
if [ "${#output}" -gt 1000 ]; then
  output="${output:0:1000}…"
fi

payload=$(jq -n \
  --arg title "${title}" \
  --arg url "${url}" \
  --arg output "${output:-(no output)}" \
  --arg kind "${kind}" \
  --arg host "${HOST_DISPLAYNAME:-$HOST_NAME}" \
  --arg state "${state}" \
  --argjson color "${color}" \
  '{
    username: "Icinga2",
    embeds: [{
      title: $title,
      url: $url,
      color: $color,
      fields: [
        { name: "Host",  value: $host,  inline: true },
        { name: "State", value: $state, inline: true },
        { name: "Output", value: $output }
      ],
      timestamp: (now | todateiso8601)
    }]
  }')

curl -fsS -H "Content-Type: application/json" \
  -X POST -d "${payload}" \
  "${DISCORD_WEBHOOK_URL}" >/dev/null
