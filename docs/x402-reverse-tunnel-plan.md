# Hyrule x402 Reverse-SSH Tunnel Plan

## Summary

Make a host behind NAT publicly reachable over a raw TCP port, leased by the
hour and paid via x402 (USDC). The host runs plain
`ssh -N -R 0:localhost:22 <lease-token>@tun.hyrule.host -p 2222` (zero install,
works from a stock rescue image) and becomes reachable at `tun.hyrule.host:<port>`.
This productizes the "server stuck in rescue mode made reachable with pinggy"
workflow.

The daemon (**`hyrule-tunnel-proxy`**) is a **second binary in the
`hyrule-network-proxy` repo**, co-located on the **`netproxy` VM** next to the
egress sidecar but running as a separate process, user, and token.

Hyrule Cloud remains responsible for the public API, x402 verification/
settlement, pricing, and lease lifecycle (DB + expiry sweep). The daemon owns the
public SSH intake, per-lease public data ports, a free STUN responder, and an
internal bearer-authed control API that Hyrule Cloud calls to mint/extend/revoke
leases.

## Fixed decisions

- **Raw TCP ports only** in v1 (no HTTP/SNI hostnames). The x402 lease token is
  the SSH username — no token-in-URL, encrypted inside the SSH transport.
- **Co-located on netproxy**, flipping it internal-only → **mixed**: only the SSH
  intake (`:2222`), STUN (`:3478/udp`), and the data-port range
  (`10000-10499`) are public; the sidecar API `:8450` and the tunnel control API
  `:8452` stay internal. netproxy is deliberately **not** in `public_facing`, so
  management SSH `:22` stays internal via `ssh_allow_sources_*`.
- **Separate binary**, because the egress sidecar's charter forbids CONNECT/TCP
  tunneling. The tunnel accepts ONLY remote (`-R`) forwarding and refuses
  `direct-tcpip`/`-L`/SOCKS/exec/shell (the egress-abuse guard, covered by a Go
  test).
- **Per-hour lease**, extendable. Default `$0.05/hr`. Chains come from backend
  config (Base/Polygon/Arbitrum USDC) — never hardcoded.
- **STUN in v1: free**, unauthenticated. Foundation for the Phase-2 NAT-type
  classifier; also unstubs Hyrule Cloud's `/v1/voip/check` STUN arm.

## Listeners (netproxy)

| Listener | Bind | Exposure | Purpose |
| --- | --- | --- | --- |
| SSH intake | `:2222` (dual-stack) | public (v6 direct + v4 via rtr DNAT) | `ssh -R`; username = lease token |
| Data ports | `10000-10499` | public | per-lease visitor traffic |
| STUN | `:3478/udp` | public | free binding responder |
| Control API | `[netproxy]:8452` | api VM only | Hyrule Cloud lease control |
| Metrics | `[netproxy]:8453` | mon only | Prometheus |

## IPv4 for rescue clients

Rescue images are often IPv4-only, so netproxy gets a static internal
`10.0.2.224/24` (`configs/netproxy/10-enX0.network`, mirroring proxy/vpn) and
rtr DNATs the public failover IPv4 for `2222/tcp`, `3478/udp`, and
`10000-10499/tcp` to it (`configs/rtr/nftables.conf` +
`roles/firewall/templates/nftables-rtr.conf.j2`, `rtr_tunnel_v4`). The return
path rides the existing `From=10.0.2.0/24 → main` VRF-leak rule (no new policy
rule). DNAT preserves the client source IP (masquerade only on WAN), so the
daemon's per-lease allowlist sees real visitor IPs. IPv6 clients hit the netproxy
GUA directly (no DNAT).

## DNS

`tun.hyrule.host` gets a single static `A 46.105.40.223` + `AAAA ::e0` in
`configs/hyrule.host.zone` (raw TCP → no per-lease DNS). Bump the SOA serial.

## Vault

New KV key `tunnel_proxy_token` in `kv/hyrule-cloud` (api VM env) and
`kv/ci-runner` (apply run), rendered via the vault-agent ctmpl templates and
enforced by `scripts/ci/deploy-preflight.sh`. Operator-seeded (≥32 chars).

## Monitoring

netproxy is registered in Icinga (`monitoring_register: true`) with a TCP check
on `:2222` and the control API `:8452`; Prometheus scrapes `[netproxy]:8453`
(`hyrule-tunnel-proxy` job — manual `prometheus.yml` edit + reload).

## Rollout (operator-gated ⛔)

1. Build the daemon (hyrule-network-proxy CI green, both binaries). No infra yet.
2. ⛔ Seed Vault `tunnel_proxy_token` (kv/hyrule-cloud + kv/ci-runner); verify
   vault-agent renders it.
3. Merge these network-operations changes (role, host_vars, netproxy v4, rtr
   DNAT, DNS serial, prometheus, CI enum, ctmpl, preflight). CI validate must
   pass (firewall render + network-flows freshness).
4. ⛔ Icinga pre-deploy snapshot (baseline before touching rtr/netproxy).
5. ⛔ Apply rtr firewall/DNAT (`playbooks/firewall.yml --tags apply`, serial:1,
   at(1) watchdog) — highest-risk step. Then apply netproxy networkd v4 +
   `tunnel-proxy.yml --tags apply` → daemon healthy.
6. ⛔ Apply DNS (Knot reload) → `tun.hyrule.host` resolves A+AAAA.
7. ⛔ Icinga post-deploy check vs baseline (TCP-2222 green, nothing newly broken).
8. Deploy hyrule-cloud behind `gate="tunnel"` (hidden until the token is present);
   keep dark.
9. Live paid canary (`x402_canary.py tunnel`, 1h lease, real USDC, self-revokes).
   Nothing announced until it passes.
10. Open the gate / announce (MCP tools + docs public).

**Rollback:** unset the token → catalog entry hidden instantly; `DELETE` all
leases via the control API; remove rtr DNAT + netproxy public firewall rules to
re-close the VM.

## Verification

- rtr apply is the riskiest step — smoke-test from a real **external IPv4**
  client (`ssh -R` in, `ssh -p <port>` from a third host, `dig tun.hyrule.host`,
  `stunclient tun.hyrule.host 3478`), not just `--check`.
- Confirm netproxy's `enX0` is on the infra bridge like proxy/vpn before applying
  the networkd v4 drop-in (netproxy's `::e0` may be set out-of-band).

## Roadmap (not built here)

- **Phase 2 — NAT-type classification (paid).** Classic 2-address STUN test needs
  a second source IP: add an alt STUN responder on `extmon` (off-net) + a
  `/v1/nat/classify` op folding in the existing CGNAT heuristic. Also add per-lease
  bandwidth limiting.
- **Phase 3 — TURN relay (paid, deferred).** coturn with x402-minted short-lived
  REST credentials; a bandwidth cost center, so gate on demand.
