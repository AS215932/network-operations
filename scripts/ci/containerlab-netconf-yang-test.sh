#!/usr/bin/env bash
# Trusted-only NETCONF/YANG lab for AS215932 FRR.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

if ! command -v containerlab >/dev/null 2>&1; then
  echo "containerlab is required for NETCONF/YANG lab tests" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for NETCONF/YANG lab tests" >&2
  exit 1
fi

topology="tests/iac/containerlab/as215932-netconf.clab.yml"
image="as215932/frr-netconf-yang:local"
artifacts="tests/iac/containerlab/_artifacts/netconf-yang"

collect_artifacts() {
  mkdir -p "$artifacts"
  sudo containerlab inspect -t "$topology" --format json >"$artifacts/inspect.json" 2>/dev/null || true
  for node in rtr cr1-nl1 cr1-de1; do
    docker logs "clab-as215932-netconf-${node}" >"$artifacts/${node}.docker.log" 2>&1 || true
    docker exec "clab-as215932-netconf-${node}" vtysh -c 'show bgp ipv6 summary json' \
      >"$artifacts/${node}.bgp-summary.json" 2>/dev/null || true
  done
}

cleanup() {
  collect_artifacts
  sudo containerlab destroy -t "$topology" --cleanup >/dev/null 2>&1 || true
}
trap cleanup EXIT

rm -rf "$artifacts"
sudo containerlab destroy -t "$topology" --cleanup >/dev/null 2>&1 || true

docker build -t "$image" tests/iac/containerlab/netconf
sudo containerlab deploy -t "$topology"

python3 tests/iac/containerlab/check_netconf_yang.py
