#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <drift-log-dir-or-log-file>" >&2
}

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

input=$1
if [ -d "$input" ]; then
  shopt -s nullglob
  logs=("$input"/*.log)
elif [ -f "$input" ]; then
  logs=("$input")
else
  echo "No drift log file or directory found: $input" >&2
  exit 0
fi

echo "## Drift summary"
echo

if [ "${#logs[@]}" -eq 0 ]; then
  echo "No drift logs found."
  exit 0
fi

echo "| Playbook | Changed tasks | Unreachable | Fatal failures |"
echo "| --- | ---: | ---: | ---: |"

tmpfiles=()
cleanup() {
  rm -f "${tmpfiles[@]}"
}
trap cleanup EXIT

for log in "${logs[@]}"; do
  playbook=$(basename "$log" .log)
  clean=$(mktemp)
  tmpfiles+=("$clean")
  perl -pe 's/\e\[[0-9;?]*[ -\/]*[@-~]//g' "$log" > "$clean"

  changed=$(grep -c 'changed: \[' "$clean" || true)
  unreachable=$(grep -c 'UNREACHABLE!' "$clean" || true)
  fatal=$(grep -E 'fatal: \[' "$clean" | grep -vc 'UNREACHABLE!' || true)

  echo "| ${playbook} | ${changed} | ${unreachable} | ${fatal} |"
done

echo
echo "### Notable failures"
echo

found=0
for log in "${logs[@]}"; do
  playbook=$(basename "$log" .log)
  clean=$(mktemp)
  tmpfiles+=("$clean")
  perl -pe 's/\e\[[0-9;?]*[ -\/]*[@-~]//g' "$log" > "$clean"

  if grep -Eq 'UNREACHABLE!|fatal: \[' "$clean"; then
    found=1
    echo "#### ${playbook}"
    echo
    grep -E 'UNREACHABLE!|fatal: \[' "$clean" | awk 'NR <= 20 { print "- " $0 }'
    echo
  fi
done

if [ "$found" -eq 0 ]; then
  echo "No unreachable hosts or fatal task failures."
fi

echo
echo "### Check-mode artifact drift"
echo

found=0
for log in "${logs[@]}"; do
  playbook=$(basename "$log" .log)
  clean=$(mktemp)
  tmpfiles+=("$clean")
  perl -pe 's/\e\[[0-9;?]*[ -\/]*[@-~]//g' "$log" > "$clean"

  if grep -q 'Would download' "$clean"; then
    found=1
    echo "#### ${playbook}"
    echo
    grep 'Would download' "$clean" | awk 'NR <= 20 { print "- " $0 }'
    echo
  fi
done

if [ "$found" -eq 0 ]; then
  echo "No missing cached release artifacts reported."
fi
