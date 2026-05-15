#!/usr/bin/env bash
# scripts/ci/test-snapshot-bracket.sh — regression test for issue #16.
#
# Runs every playbook in `--tags snapshot,validate --connection=local
# --check --limit <something>` and grep-asserts that the Pre-deploy and
# Post-deploy Icinga snapshot tasks appear in the dry-run output.
#
# Catches the failure mode where a future refactor accidentally puts
# snapshots back into a standalone `hosts: localhost` play (which gets
# filtered out by --limit).

set -euo pipefail

cd "$(dirname "$0")/../../ansible"

# Per-playbook test limit. Picks a host that's actually in each playbook's
# main hosts: selector. The point of the test is "snapshot fires under
# --limit", so the limit must match at least one host in the play.
declare -A test_limits=(
  [firewall]=noc
  [monitoring]=noc
  [icinga2]=mon
  [logs]=noc
  [noc]=noc
  [vault]=vault
  [knot]=dns
  [networkd_resolved]=noc
  [mail_openbsd]=mail
)

fail=0
for pb in "${!test_limits[@]}"; do
  limit="${test_limits[$pb]}"
  echo "::group::test bracket: ${pb}.yml --limit ${limit}"
  out=$(
    ansible-playbook "playbooks/${pb}.yml" \
      --tags snapshot,always \
      --connection=local \
      --check \
      --limit "${limit}" 2>&1 || true
  )
  if ! grep -q 'Pre-deploy Icinga snapshot' <<<"${out}"; then
    echo "::error::${pb}.yml does NOT trigger pre-deploy snapshot under --limit ${limit}"
    fail=1
  fi
  if ! grep -q 'Post-deploy Icinga snapshot' <<<"${out}"; then
    echo "::error::${pb}.yml does NOT trigger post-deploy snapshot under --limit ${limit}"
    fail=1
  fi
  echo "::endgroup::"
done

exit "${fail}"
