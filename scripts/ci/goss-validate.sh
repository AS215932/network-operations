#!/usr/bin/env bash
# Post-deploy validation wrapper. The apply workflow calls this after Ansible
# so host-specific Goss specs can become a hard gate as coverage grows.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
playbook="${1:-}"
limit="${2:-}"

if ! command -v goss >/dev/null 2>&1; then
  echo "::warning::goss is not installed on the runner; install goss before making postflight validation required"
  exit 0
fi

case "$playbook" in
  ci)
    spec="$repo_root/tests/goss/ci-runner.yml"
    ;;
  cloud)
    echo "::notice::hyrule-cloud Goss spec is staged, but target-side execution is paused with the cloud/web refactor"
    exit 0
    ;;
  rtr_routing|firewall)
    echo "::notice::router Goss spec is staged; remote target execution must be wired before making it a hard apply gate"
    exit 0
    ;;
  *)
    echo "::notice::no Goss spec mapped for playbook=${playbook} limit=${limit}; skipping"
    exit 0
    ;;
esac

goss -g "$spec" validate --format documentation
