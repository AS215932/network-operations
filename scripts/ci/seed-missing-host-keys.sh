#!/usr/bin/env bash
# Seed the runner's known_hosts with keys for inventory hosts that are missing
# from it, so a freshly added host (PR-reviewed inventory change) is reachable
# on the next drift/apply run without the manual `apply.yml ci` reseed that
# cr1-ch1 and extmon needed (network-operations#404).
#
# Security: only ADDS entries for hosts with no known_hosts line at all —
# never rewrites existing keys, so a changed host key still fails loudly.
# The trust-on-first-scan here is equivalent to the github_runner role's
# full-fleet ssh-keyscan seeding.
#
# Usage: seed-missing-host-keys.sh   (run from the repo root; needs
# ansible-inventory + jq, both present on the ci runner)

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
known_hosts="${KNOWN_HOSTS_FILE:-$HOME/.ssh/known_hosts}"

mkdir -p "$(dirname "$known_hosts")"
touch "$known_hosts"

targets="$(cd "$repo_root/ansible" && ansible-inventory --list 2>/dev/null \
  | jq -r '._meta.hostvars | to_entries[] | .value.ansible_host // .key' \
  | sort -u)"

added=0
for target in $targets; do
  case "$target" in
    0.0.0.0|::|localhost|127.*) continue ;;
  esac
  # Canonicalize IP targets (e.g. zero-padded IPv6 groups): ssh-keyscan writes
  # the canonical form into known_hosts, so ssh-keygen -F must look up the
  # same form or the host is re-scanned (and duplicated) on every run.
  target="$(python3 -c "import ipaddress,sys
try: print(ipaddress.ip_address(sys.argv[1]))
except ValueError: print(sys.argv[1])" "$target")"
  if ssh-keygen -F "$target" -f "$known_hosts" >/dev/null 2>&1; then
    continue
  fi
  if ssh-keyscan -T 5 "$target" >> "$known_hosts" 2>/dev/null; then
    echo "seeded host key(s) for ${target}"
    added=$((added + 1))
  else
    echo "::warning::could not keyscan ${target} — host unreachable or ssh down"
  fi
done

echo "known_hosts seeding done (${added} new host(s))"
