# Stockholm inference proxy (inf-sthlm)

Design for serving Qwen3.8-27B FP8 (vLLM, H100 SXM) under AS215932. **Not deployed;
host not yet in inventory.** Files: `configs/inf-sthlm/`.

## Path
Client (v4/v6) -> Cloudflare -> AAAA origin `inf-sthlm:443` -> nginx -> `wg0` -> vLLM `:8000` on GPU nodes.

- OpenAI-compatible `POST /v1/chat/completions` and `GET /v1/models`; everything else 404.
- SSE: `proxy_buffering off`, `proxy_request_buffering off`, 600 s read/send timeouts.
  Cloudflare drops idle connections after ~100 s, so clients must use `stream: true` for long generations.
- x402 headers pass through; x402 pricing per million tokens plugs in in front of `upstream vllm`
  (sidecar per docs/x402-network-proxy-sidecar-plan.md) in a follow-up.

## Origin protection
- pf table `<cloudflare6>` loaded from `/etc/pf.cloudflare6` (= `configs/inf-sthlm/cloudflare-ips-v6`); 443 only from it.
- No IPv4 listener on 443.
- Cloudflare Authenticated Origin Pulls: `ssl_verify_client on` against Cloudflare's origin-pull CA.
  Enable "Authenticated Origin Pulls" in the zone (SSL/TLS -> Origin Server) and set SSL mode Full (strict).
- Refresh ranges: `scripts/update-cloudflare-ips-v6.sh`, then `pfctl -t cloudflare6 -T replace -f /etc/pf.cloudflare6`.

## GPU overlay addressing
- **`2a0c:b641:b50:10::/64`** — new, GPU inference overlay (unused in the plan: `:0` mgmt, `:1` vpn transit,
  `:2` infra, `:3` VPN clients, `:f0` loop docker, `ff00::/56` router links).
  - `::1` inf-sthlm, `::11`-`::ff` GPU nodes.
- Underlay is IPv4 (vast.ai is v4-only behind NAT); nodes dial out with keepalive 25. Inner is IPv6 only. MTU 1380.
- Routing of the /64 into the rest of AS215932 (rtr/core FRR) is intentionally out of scope.

## Placeholders
- `<STHLM_V4_UNDERLAY>` (Noack v4, or the future own /24), public AAAA of inf-sthlm, DNS name `infer.as215932.net`.
- WireGuard keys/PSKs (generate on host; private keys never in git).
- Cloudflare Origin CA cert/key and `cloudflare-origin-pull-ca.pem`.
- Interface names `vio0`/`wg0` (OpenBSD assumed, matching pf).
