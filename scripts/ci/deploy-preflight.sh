#!/usr/bin/env bash
# CI/deploy contract checks that must pass before an apply job is allowed to
# touch production. The default mode is repo-only so it is safe to run locally.

set -euo pipefail

mode="${1:---repo-only}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail=0

error() {
  echo "::error::$*" >&2
  fail=1
}

warn() {
  echo "::warning::$*" >&2
}

require_file() {
  local path="$1"
  if [[ ! -e "$repo_root/$path" ]]; then
    error "missing required file: $path"
  fi
}

check_repo_contracts() {
  require_file "ansible/roles/vault_agent/templates/hyrule-cloud.env.ctmpl.j2"
  require_file "configs/vault/policies/hyrule-cloud.hcl"
  require_file "ansible/roles/hyrule_cloud/tasks/vault.yml"

  if grep -RInE "lookup\\('env', *'XO_TOKEN'\\)|lookup\\(\"env\", *\"XO_TOKEN\"\\)|xo_token" \
      "$repo_root/ansible/roles/hyrule_cloud" "$repo_root/ansible/playbooks/cloud.yml"; then
    error "hyrule-cloud deploy still depends on runner-side XO_TOKEN/xo_token"
  fi

  if grep -InE "XO_TOKEN|xo_token" "$repo_root/ansible/roles/vault_agent/templates/github-runner.env.ctmpl.j2"; then
    error "github-runner Vault template must not render XO_TOKEN"
  fi

  for key in \
    xo_token sr_uuid vm_network_uuid xcpng_templates \
    openprovider_username openprovider_password \
    openprovider_owner_handle openprovider_admin_handle \
    openprovider_tech_handle openprovider_billing_handle \
    payment_wallet tsig_secret db_password; do
    if ! grep -q "\.Data\.data\.${key}" "$repo_root/ansible/roles/vault_agent/templates/hyrule-cloud.env.ctmpl.j2"; then
      error "hyrule-cloud Vault template does not reference kv/data/hyrule-cloud key: ${key}"
    fi
  done

  for label in self-hosted linux x64 hyrule hyrule-infra; do
    if ! grep -Eq "^[[:space:]]*-[[:space:]]*(\"\\{\\{ github_runner_arch \\}\\}\"|${label})$" \
        "$repo_root/ansible/roles/github_runner/defaults/main.yml"; then
      if [[ "$label" == "x64" ]]; then
        continue
      fi
      error "github_runner_labels is missing ${label}"
    fi
  done
}

check_runner_host() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active --quiet github-runner.service || error "github-runner.service is not active"
    systemctl is-active --quiet vault-agent-github-runner.service || error "vault-agent-github-runner.service is not active"
  else
    warn "systemctl unavailable; skipping live runner service checks"
  fi

  local secrets_env=/etc/github-runner/secrets.env
  if [[ ! -r "$secrets_env" ]]; then
    error "$secrets_env is not readable"
  else
    local mode owner group
    mode="$(stat -c '%a' "$secrets_env")"
    owner="$(stat -c '%U' "$secrets_env")"
    group="$(stat -c '%G' "$secrets_env")"
    [[ "$mode" == "640" ]] || error "$secrets_env mode is $mode, expected 640"
    [[ "$owner" == "root" ]] || error "$secrets_env owner is $owner, expected root"
    [[ "$group" == "runner" ]] || error "$secrets_env group is $group, expected runner"
    if grep -Eq '^(XO_TOKEN|XCPNG_XO_TOKEN)=' "$secrets_env"; then
      error "$secrets_env must not contain XO_TOKEN/XCPNG_XO_TOKEN"
    fi
  fi

  local runner_file=/var/lib/github-runner/runner/.runner
  if [[ -r "$runner_file" ]]; then
    for label in self-hosted linux x64 hyrule hyrule-infra; do
      grep -q "\"${label}\"" "$runner_file" || error "registered runner label missing: ${label}"
    done
  else
    warn "$runner_file not readable; skipping registered label check"
  fi
}

check_repo_contracts

case "$mode" in
  --repo-only)
    ;;
  --runner)
    check_runner_host
    ;;
  *)
    echo "usage: $0 [--repo-only|--runner]" >&2
    exit 2
    ;;
esac

exit "$fail"
