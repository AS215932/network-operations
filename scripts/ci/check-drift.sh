#!/usr/bin/env bash
# Run the production drift sweep: ansible-playbook --check --diff for each
# playbook, one log per playbook, non-zero exit when anything drifted or
# failed. Shared by drift-detection.yml (nightly) and post-merge-apply.yml
# (push to main) so the two can never diverge on scope or semantics.
#
# Usage (CWD must be ansible/):
#   ../scripts/ci/check-drift.sh <log-dir> [playbook ...]
#
# With no playbook arguments the canonical sweep below runs. The limit can be
# narrowed via CHECK_DRIFT_LIMIT (default all:!ci-pr) — used by the post-merge
# verify pass to re-check only the hosts it just applied.
#
# ci-pr lives on the customer-isolated segment and is managed from the ops
# workstation, not from the privileged ci runner — hence the default limit.

set -uo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <log-dir> [playbook ...]" >&2
  exit 2
fi

logdir=$1
shift
# The canonical sweep. tests/iac/test_drift_detection.py guards this list —
# removing an entry fails CI.
default_playbooks=(firewall monitoring logs icinga2 prometheus alertmanager ci rtr_routing networkd_resolved extmon)

playbooks=("$@")
if [ "${#playbooks[@]}" -eq 0 ]; then
  playbooks=("${default_playbooks[@]}")
fi

limit="${CHECK_DRIFT_LIMIT:-all:!ci-pr}"

mkdir -p "$logdir"
status=0
for playbook in "${playbooks[@]}"; do
  echo "::group::drift ${playbook}"
  log="${logdir}/${playbook}.log"
  if ! ansible-playbook "playbooks/${playbook}.yml" \
      --check --diff \
      --tags apply \
      --limit "$limit" \
      -e ansible_user=ci \
      -e "${playbook}_apply=true" 2>&1 | tee "${log}"; then
    status=1
  fi
  if grep -q 'changed: \[' "${log}"; then
    echo "::error::${playbook} reported check-mode changes"
    status=1
  fi
  echo "::endgroup::"
done
exit "$status"
