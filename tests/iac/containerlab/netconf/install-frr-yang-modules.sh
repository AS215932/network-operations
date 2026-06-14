#!/usr/bin/env bash
# Install the FRR YANG module set needed by the NETCONF/YANG lab into sysrepo.
# This script is lab-only and runs inside the Containerlab image.

set -euo pipefail

YANG_DIR="${YANG_DIR:-/usr/share/yang}"
cd "$YANG_DIR"

install_module() {
  local module_path="$1"
  local module_name
  module_name="$(basename "$module_path" .yang)"

  if [ ! -f "$module_path" ]; then
    echo "missing YANG module: $module_path" >&2
    return 1
  fi

  if sysrepoctl -l 2>/dev/null | awk '{print $1}' | grep -qx "$module_name"; then
    sysrepoctl -c "$module_name" -o frr -g frr >/dev/null 2>&1 || true
    return 0
  fi

  sysrepoctl -i "$module_path" -s "$YANG_DIR" -o frr -g frr
}

# IETF/sysrepo dependencies first.
modules=(
  ietf/ietf-interfaces.yang
  ietf/ietf-routing-types.yang
  ietf/ietf-bgp-types.yang
  ietf/ietf-netconf-acm.yang
  ietf/ietf-netconf.yang
  ietf/ietf-netconf-with-defaults.yang
  ietf/ietf-srv6-types.yang
  frr-vrf.yang
  frr-route-types.yang
  frr-interface.yang
  frr-routing.yang
  frr-filter.yang
  frr-route-map.yang
  frr-nexthop.yang
  frr-if-rmap.yang
  frr-affinity-map.yang
  frr-bfdd.yang
  frr-bgp-types.yang
  frr-bgp-filter.yang
  frr-bgp-route-map.yang
  frr-bgp.yang
  frr-staticd.yang
  frr-zebra-route-map.yang
  frr-zebra.yang
  frr-ospf-route-map.yang
  frr-ospf6-route-map.yang
)

for module in "${modules[@]}"; do
  install_module "$module"
done

sysrepoctl -l | grep -E '(^frr-(bgp|interface|route-map|zebra|staticd)\b|^ietf-netconf\b)'
