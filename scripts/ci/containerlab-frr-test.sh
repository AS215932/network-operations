#!/usr/bin/env bash
# Dynamic FRR smoke test for the core AS215932 topology.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

if ! command -v containerlab >/dev/null 2>&1; then
  echo "containerlab is required for dynamic FRR tests" >&2
  exit 1
fi

topology="tests/iac/containerlab/as215932.clab.yml"

# containerlab needs root to create netns/veth; the runner runs unprivileged
# (UID 995). It's granted passwordless `sudo containerlab` via the github_runner
# role (clab_admins + /etc/sudoers.d/github-runner-containerlab). See
# network-operations#143.
cleanup() {
  sudo containerlab destroy -t "$topology" --cleanup >/dev/null 2>&1 || true
}
trap cleanup EXIT

sudo containerlab deploy -t "$topology"

for node in rtr cr1-nl1 cr1-de1; do
  docker exec "clab-as215932-${node}" vtysh -c 'show bgp summary'
done

python3 tests/iac/containerlab/check_frr_lab.py
