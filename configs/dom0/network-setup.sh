#!/bin/sh
# dom0 (XCP-NG) network setup for rtr IPv6 WAN connectivity
# Run on dom0 after boot to enable rtr's OVH underlay access.
#
# OVH MAC-filters their switch — only the physical NIC's MAC is allowed.
# dom0 acts as NDP proxy and IPv6 gateway for rtr's underlay address.

set -e

# Enable IPv6 forwarding and NDP proxy
sysctl -w net.ipv6.conf.all.forwarding=1
sysctl -w net.ipv6.conf.all.proxy_ndp=1

# dom0 takes ::1 on the OVH /64
ip -6 addr add 2001:41d0:303:48a::1/64 dev xenbr0 2>/dev/null || true

# OVH gateway (static NDP entry — MAC from OVH control panel)
ip -6 neigh replace 2001:41d0:303:4ff:ff:ff:ff:ff dev xenbr0 lladdr 00:05:73:a0:00:00
ip -6 route replace 2001:41d0:303:4ff:ff:ff:ff:ff dev xenbr0
ip -6 route replace default via 2001:41d0:303:4ff:ff:ff:ff:ff dev xenbr0

# NDP proxy for rtr's underlay address (so OVH switch sees replies from dom0's MAC)
ip -6 neigh add proxy 2001:41d0:303:48a::2 dev xenbr0 2>/dev/null || true

# dom0 is underlay-only — no AS215932 addresses.
# mgmt bridge (xapi0) uses link-local IPv6 + 10.0.0.1/24 for XOA.

echo "dom0 IPv6 forwarding configured for rtr underlay"
