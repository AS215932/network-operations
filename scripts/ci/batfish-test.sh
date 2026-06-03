#!/usr/bin/env bash
# Run Batfish network-model tests. If BATFISH_HOST is unset and Docker is
# available, this script starts a temporary all-in-one Batfish container.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

container_name="${BATFISH_CONTAINER_NAME:-hyrule-batfish}"
started_container=0

cleanup() {
  if [[ "$started_container" == "1" ]]; then
    docker rm -f "$container_name" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

python3 - <<'PY'
import importlib.util
import sys
if importlib.util.find_spec("pytest") is None:
    print("pytest is required for Batfish tests", file=sys.stderr)
    sys.exit(1)
if importlib.util.find_spec("pybatfish") is None:
    print("pybatfish is required for Batfish tests", file=sys.stderr)
    sys.exit(1)
PY

if [[ -z "${BATFISH_HOST:-}" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "BATFISH_HOST is unset and docker is unavailable" >&2
    exit 1
  fi
  docker rm -f "$container_name" >/dev/null 2>&1 || true
  # --network host (not -p) so Batfish binds 9997/9996 directly on the host: the
  # self-hosted runner's Docker daemon can't program the iptables DOCKER nat chain
  # for published ports, which fails `-p` with "iptables: No chain/target/match"
  # (network-operations#143). Host networking needs no DNAT, so it sidesteps that.
  docker run -d --name "$container_name" --network host batfish/allinone >/dev/null
  started_container=1
  export BATFISH_HOST=127.0.0.1
  sleep 15
fi

python3 -m pytest tests/iac/batfish
