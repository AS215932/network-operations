#!/usr/bin/env bash
# Post-deploy validation wrapper. The apply workflow calls this after Ansible
# so host-specific Goss specs can become a hard gate as coverage grows.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
playbook="${1:-}"
limit="${2:-}"

case "$playbook" in
  ci)
    if ! command -v goss >/dev/null 2>&1; then
      echo "::warning::goss is not installed on the runner; install goss before making postflight validation required"
      exit 0
    fi
    spec="$repo_root/tests/goss/ci-runner.yml"
    ;;
  cloud)
    limit_args=()
    if [[ -n "$limit" ]]; then
      limit_args=(--limit "$limit")
    fi
    cd "$repo_root/ansible"
    exec ansible-playbook playbooks/goss_cloud.yml \
      -e ansible_user=ci \
      "${limit_args[@]}"
    ;;
  rtr_routing|firewall|frr)
    echo "::notice::router Goss spec is staged; remote target execution must be wired before making it a hard apply gate"
    exit 0
    ;;
  *)
    echo "::notice::no Goss spec mapped for playbook=${playbook} limit=${limit}; skipping"
    exit 0
    ;;
esac

goss -g "$spec" validate --format documentation
