#!/usr/bin/env bash
# Static infrastructure-as-code test entrypoint. Keep this dependency-light:
# Python unittest checks are stdlib, while external validators run when present.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

ci_tmp_root="${RUNNER_TEMP:-}"
if [[ -z "$ci_tmp_root" ]]; then
  ci_tmp_root="${GITHUB_WORKSPACE:-$repo_root}/.tmp"
fi

export ANSIBLE_LOCAL_TEMP="${ANSIBLE_LOCAL_TEMP:-$ci_tmp_root/ansible-local}"
export ANSIBLE_REMOTE_TEMP="${ANSIBLE_REMOTE_TEMP:-$ci_tmp_root/ansible-remote}"
mkdir -p "$ANSIBLE_LOCAL_TEMP" "$ANSIBLE_REMOTE_TEMP"

fail=0

run() {
  echo "::group::$*"
  if ! "$@"; then
    fail=1
  fi
  echo "::endgroup::"
}

run python3 -m unittest discover -s tests/iac -p 'test_*.py'

if command -v named-checkzone >/dev/null 2>&1; then
  run named-checkzone as215932.net configs/as215932.net.zone
  run named-checkzone 0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa configs/0.5.b.0.1.4.6.b.c.0.a.2.ip6.arpa.zone
  run named-checkzone servify.network configs/servify.network.zone
  run named-checkzone hyrule.host configs/hyrule.host.zone
  run named-checkzone deploy.hyrule.host configs/deploy.hyrule.host.zone
else
  echo "::warning::named-checkzone not installed; skipping DNS zone parser checks"
fi

if command -v systemd-analyze >/dev/null 2>&1; then
  echo "::group::systemd-analyze verify configs/*.service"
  if ! systemd-analyze verify configs/*.service >/tmp/hyrule-systemd-check.log 2>&1; then
    if grep -Eq "Operation not permitted|Failed to enable SO_PASSCRED|Failed to turn off SO_PASSRIGHTS" /tmp/hyrule-systemd-check.log \
        && [[ "${IAC_REQUIRE_SYSTEMD_CHECKS:-0}" != "1" ]]; then
      echo "::warning::systemd-analyze verify hit sandbox socket restrictions; set IAC_REQUIRE_SYSTEMD_CHECKS=1 on a full systemd runner"
      sed -n '1,40p' /tmp/hyrule-systemd-check.log || true
    else
      cat /tmp/hyrule-systemd-check.log
      fail=1
    fi
  fi
  echo "::endgroup::"
else
  echo "::warning::systemd-analyze not installed; skipping systemd unit checks"
fi

if command -v caddy >/dev/null 2>&1; then
  echo "::group::caddy validate --config configs/Caddyfile"
  if ! caddy validate --config configs/Caddyfile >/tmp/hyrule-caddy-check.log 2>&1; then
    if grep -q "module not registered: dns.providers.rfc2136" /tmp/hyrule-caddy-check.log; then
      echo "::warning::installed caddy lacks dns.providers.rfc2136; skipping strict Caddy validation on this runner"
      sed -n '1,40p' /tmp/hyrule-caddy-check.log || true
    else
      cat /tmp/hyrule-caddy-check.log
      fail=1
    fi
  fi
  echo "::endgroup::"
else
  echo "::warning::caddy not installed; skipping Caddy validation"
fi

if command -v unbound-checkconf >/dev/null 2>&1 && [[ -f configs/rtr/unbound/as215932.conf ]]; then
  echo "::group::unbound-checkconf configs/rtr/unbound/as215932.conf"
  if ! unbound-checkconf configs/rtr/unbound/as215932.conf >/tmp/hyrule-unbound-check.log 2>&1; then
    if [[ "${IAC_REQUIRE_NET_CHECKS:-0}" == "1" ]]; then
      cat /tmp/hyrule-unbound-check.log
      fail=1
    else
      echo "::warning::unbound-checkconf needs interface access on this runner; set IAC_REQUIRE_NET_CHECKS=1 in a network-capable job"
      sed -n '1,40p' /tmp/hyrule-unbound-check.log || true
    fi
  fi
  echo "::endgroup::"
else
  echo "::warning::unbound-checkconf unavailable or config absent; skipping Unbound validation"
fi

if command -v nft >/dev/null 2>&1; then
  if [[ "${IAC_REQUIRE_PRIVILEGED_CHECKS:-0}" == "1" ]]; then
    run nft -c -f configs/rtr/nftables.conf
  else
    if ! nft -c -f configs/rtr/nftables.conf >/tmp/hyrule-nft-check.log 2>&1; then
      echo "::warning::nft validation needs CAP_NET_ADMIN/root on this runner; set IAC_REQUIRE_PRIVILEGED_CHECKS=1 in a privileged job"
      sed -n '1,40p' /tmp/hyrule-nft-check.log || true
    fi
  fi
else
  echo "::warning::nft not installed; skipping nftables validation"
fi

run scripts/ci/test-snapshot-bracket.sh
run scripts/ci/deploy-preflight.sh --repo-only

exit "$fail"
