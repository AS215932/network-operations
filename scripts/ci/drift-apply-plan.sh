#!/usr/bin/env bash
# Turn a drift-log directory (from check-drift.sh) into an apply plan:
#
#   {"apply":   [{"playbook": "firewall", "limit": "cr1-nl1:cr1-de1"}, ...],
#    "skipped": [{"playbook": "ci", "host": "ci", "reason": "..."}, ...]}
#
# A host is auto-applicable when its PLAY RECAP line shows changed>0 with
# unreachable=0 and failed=0. Everything else (unreachable, failed, and the
# whole `ci` playbook — applying the runner's own playbook from the runner can
# restart the runner service mid-job) is listed under `skipped` for a human.
#
# Usage: drift-apply-plan.sh <log-dir>

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <log-dir>" >&2
  exit 2
fi

logdir=$1
shopt -s nullglob
logs=("$logdir"/*.log)

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

for log in "${logs[@]}"; do
  playbook="$(basename "$log" .log)"
  # Strip ANSI colour codes, then read only the PLAY RECAP section.
  perl -pe 's/\e\[[0-9;?]*[ -\/]*[@-~]//g' "$log" | awk -v pb="$playbook" '
    /PLAY RECAP/ { in_recap = 1; next }
    in_recap && $2 == ":" && $3 ~ /^ok=/ {
      host = $1
      changed = unreachable = failed = 0
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^changed=/)     { sub("changed=", "", $i);     changed = $i + 0 }
        if ($i ~ /^unreachable=/) { sub("unreachable=", "", $i); unreachable = $i + 0 }
        if ($i ~ /^failed=/)      { sub("failed=", "", $i);      failed = $i + 0 }
      }
      if (host == "localhost") next
      if (unreachable > 0)      { print pb "\t" host "\tskip\tunreachable" }
      else if (failed > 0)      { print pb "\t" host "\tskip\tfailed tasks" }
      else if (changed > 0) {
        if (pb == "ci")         { print pb "\t" host "\tskip\tci playbook is never auto-applied (runner self-restart risk)" }
        else                    { print pb "\t" host "\tapply\t-" }
      }
    }
  ' >> "$tmp"
done

jq -R -s '
  split("\n") | map(select(length > 0) | split("\t")) |
  {
    apply: (map(select(.[2] == "apply")) | group_by(.[0]) |
            map({playbook: .[0][0], limit: (map(.[1]) | join(":"))})),
    skipped: (map(select(.[2] == "skip")) |
              map({playbook: .[0], host: .[1], reason: .[3]}))
  }
' < "$tmp"
