# AS215932 VPN client

The client is an IPv6-only full tunnel. IPv4 must fail closed instead of using
the local ISP. Do not put `0.0.0.0/0` in WireGuard `AllowedIPs`: that is
cryptokey routing to a peer which accepts no IPv4, not a null route.

For NetworkManager, keep `AllowedIPs = ::/0` and install a native IPv4
`blackhole` route in a policy table. A more-specific `throw` route for the
WireGuard endpoint makes that one lookup continue to the current ISP default;
every other IPv4 destination is dropped. This avoids a fixed local gateway and
does not depend on the WireGuard socket mark for IPv4 leak prevention.

Apply these persistent connection properties to the existing `as215932`
profile while it is disconnected:

```bash
nmcli connection modify as215932 \
  ipv4.method manual \
  ipv4.addresses "" \
  ipv4.gateway "" \
  ipv4.route-table 333856 \
  ipv4.routes \
    "0.0.0.0/0 type=blackhole,46.105.40.223/32 type=throw" \
  ipv4.routing-rules "priority 31021 from all table 333856" \
  ipv4.never-default yes \
  ipv4.ignore-auto-routes yes \
  ipv4.ignore-auto-dns yes \
  ipv6.dns "2001:4860:4860::64 2001:4860:4860::6464" \
  ipv6.dns-priority -999 \
  wireguard.fwmark 0x51820 \
  wireguard.ip4-auto-default-route no \
  wireguard.ip6-auto-default-route yes \
  wireguard.peer-routes yes \
  wireguard.peers \
    "h4AjH9BV72VJEZhiUMZANfIdrzKxX1NQ8JaBJYFH71U= allowed-ips=::/0 endpoint=46.105.40.223:51820 persistent-keepalive=25"
```

NetworkManager persists those properties in the connection profile and adds
and removes the routes and rule with the VPN. No dispatcher, `PostUp`, fixed
local gateway, or manually managed unicast endpoint route is required.

Verify after connecting:

```bash
ip -4 rule show
ip -4 route show table 333856
ip -4 route get 46.105.40.223
ping -4 -c 1 1.1.1.1             # must fail
ping -6 -c 1 2001:4860:4860::64  # must pass
```

Expected IPv4 state:

```text
31021: from all lookup 333856
throw 46.105.40.223
blackhole default
```
