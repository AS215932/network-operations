# Hyrule x402 Network Proxy Sidecar Plan

## Summary

Build a new standalone Go service/repo named **`hyrule-network-proxy`**. It will run on a dedicated **`netproxy` VM** and provide internal-only, paid-request egress for Hyrule Cloud's existing x402-gated `POST /v1/network/request` endpoint.

Hyrule Cloud remains responsible for:

- public API contract;
- x402 payment verification and settlement;
- pricing;
- request validation visible to clients;
- no-payment `402` flow.

The Go sidecar is responsible for:

- executing the network request;
- enforcing egress policy;
- routing through `direct`, `tor`, `i2p`, or `yggdrasil`;
- truncating/redacting responses;
- exposing mode health to Hyrule Cloud before payment.

The sidecar must **not** be called `proxy-cheap`, and the `residential` mode must be removed from the advertised product contract.

## Fixed Decisions

- New repo name: `hyrule-network-proxy`
- GitHub org/repo: `AS215932/hyrule-network-proxy`
- Go module: `github.com/AS215932/hyrule-network-proxy`
- Deployment topology: dedicated VM named `netproxy`
- Proposed netproxy IPv6: `2a0c:b641:b50:2::e0`
- Internal identity/domain: `netproxy.servify.network`
- Hyrule Cloud public endpoint remains: `POST /v1/network/request`
- Public proxy modes become:
  - `direct`
  - `tor`
  - `i2p`
  - `yggdrasil`
- Remove/stop advertising:
  - `residential`
- Internal sidecar protocol: HTTP JSON
- Internal sidecar auth: shared bearer token from Vault
- Sidecar API listener: TCP `8450`, accessible only from `api`
- Sidecar metrics/health listener: TCP `8451`, accessible only from `mon`
- x402 stays entirely in Hyrule Cloud; no payment data is passed to the sidecar.
- MVP allows `GET`, `HEAD`, and `POST`.

## Implementation Steps

1. [x] Create the new `hyrule-network-proxy` Go repo. Initial local commit: `fad1d9488ea8cc9c319e2af37899f8dc06e79bec`.
2. [x] Implement the sidecar internal HTTP API, routing modes, safety policy, logging, health, and metrics. Current sidecar commit: `b82dc72bbf382167062bff272606ce84ec20538c`.
3. [x] Update `hyrule-cloud` API contract and provider to call the sidecar. Hyrule Cloud commit: `a1a3b3081172efecd72368f25351413ad84234cb`.
4. [x] Update `hyrule-cloud` pricing, manifest, tests, and docs for `direct`/`tor`/`i2p`/`yggdrasil`. Hyrule Cloud commit: `a1a3b3081172efecd72368f25351413ad84234cb`.
5. [x] Update `hyrule-infra` to provision and deploy the new `netproxy` VM/service. Hyrule Infra commit: `69f81c4`.
6. [x] Add monitoring, logging, firewall, Vault, and deployment-promotion wiring. Hyrule Infra commit: `69f81c4`.
7. [ ] Validate with unit tests, integration tests, infra render tests, smoke tests, and staged rollout.

## Current State

### In `hyrule-cloud`

Current public route:

```http
POST /v1/network/request
```

Current models:

```python
class ProxyMode(enum.StrEnum):
    DIRECT = "direct"
    TOR = "tor"
    RESIDENTIAL = "residential"

class NetworkRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] | None = None
    body: str | None = None
    proxy_mode: ProxyMode = ProxyMode.DIRECT
    timeout_seconds: int = 15

class NetworkResponse(BaseModel):
    status_code: int
    headers: dict[str, str]
    body: str
    elapsed_seconds: float
    proxy_mode: ProxyMode
    error: str | None = None
```

Current provider:

- `hyrule_cloud/providers/network_client.py`
- does direct and Tor in Python via `httpx`
- returns `501` for residential
- has partial SSRF protection
- uses local compose `dperson/torproxy`

This will be replaced by a sidecar client.

## New Public Hyrule Cloud API Contract

### Public request

`POST /v1/network/request`

```json
{
  "url": "https://example.com",
  "method": "GET",
  "headers": {
    "accept": "text/html"
  },
  "body": null,
  "proxy_mode": "tor",
  "timeout_seconds": 15
}
```

### Public response

```json
{
  "status_code": 200,
  "headers": {
    "content-type": "text/html; charset=utf-8"
  },
  "body": "<html>...</html>",
  "elapsed_seconds": 0.421,
  "proxy_mode": "tor",
  "error": null
}
```

### Public proxy modes

Update `ProxyMode` to:

```python
class ProxyMode(enum.StrEnum):
    DIRECT = "direct"
    TOR = "tor"
    I2P = "i2p"
    YGGDRASIL = "yggdrasil"
```

Remove `RESIDENTIAL` from:

- model enum;
- pricing;
- x402 manifest;
- docs;
- tests;
- frontend/client references, if any.

### Public request limits

Keep or ensure:

```python
url: max_length=2048
method: GET | HEAD | POST
body: max_length=65536
timeout_seconds: 1..60
```

### Public route behavior

Before requesting x402 payment, Hyrule Cloud must:

1. validate `proxy_mode`;
2. ask sidecar mode health/cache whether that mode is available;
3. if unavailable, return `503` and **do not charge**;
4. if available and no valid payment, return `402`;
5. if payment valid, call sidecar;
6. return sidecar `NetworkResponse`.

Error behavior:

- invalid method/scheme/mode: public HTTP `400`
- SSRF/policy denied: public HTTP `403`
- mode unavailable before payment: public HTTP `503`
- sidecar auth/config failure: public HTTP `502`
- upstream network failure after payment: public HTTP `200` with embedded:

```json
{
  "status_code": 502,
  "error": "Network error: ..."
}
```

This preserves the existing “upstream status lives inside `NetworkResponse`” shape.

## New Sidecar Internal API

### Base URL

From Hyrule Cloud:

```text
http://[2a0c:b641:b50:2::e0]:8450
```

Configured via:

```env
HYRULE_NETWORK_PROXY_URL=http://[2a0c:b641:b50:2::e0]:8450
HYRULE_NETWORK_PROXY_TOKEN=<vault-secret>
HYRULE_NETWORK_PROXY_HEALTH_TTL_SECONDS=15
```

### Authentication

Every internal API request from Hyrule Cloud to sidecar includes:

```http
Authorization: Bearer <HYRULE_NETWORK_PROXY_TOKEN>
```

The sidecar returns `401` for missing/wrong token.

### `GET /v1/health`

Authenticated.

Response:

```json
{
  "status": "ok",
  "service": "hyrule-network-proxy",
  "version": "git-sha-or-build-version"
}
```

### `GET /v1/modes`

Authenticated.

Response:

```json
{
  "modes": {
    "direct": {
      "available": true,
      "reason": null
    },
    "tor": {
      "available": true,
      "reason": null
    },
    "i2p": {
      "available": true,
      "reason": null
    },
    "yggdrasil": {
      "available": false,
      "reason": "no yggdrasil peers configured"
    }
  }
}
```

Hyrule Cloud caches this for `HYRULE_NETWORK_PROXY_HEALTH_TTL_SECONDS`.

### `POST /v1/request`

Authenticated.

Internal request:

```json
{
  "request_id": "uuid-or-hyrule-generated-id",
  "url": "http://example.i2p/",
  "method": "GET",
  "headers": {
    "accept": "text/html"
  },
  "body": null,
  "proxy_mode": "i2p",
  "timeout_seconds": 15
}
```

Internal response is exactly the public `NetworkResponse` shape:

```json
{
  "status_code": 200,
  "headers": {
    "content-type": "text/html"
  },
  "body": "...",
  "elapsed_seconds": 0.312,
  "proxy_mode": "i2p",
  "error": null
}
```

Handled policy/network failures return HTTP `200` with embedded status/error. Only sidecar server/auth failures return non-200 HTTP statuses.

## Sidecar Repo Layout

Create:

```text
hyrule-network-proxy/
├── AGENTS.md
├── README.md
├── go.mod
├── go.sum
├── cmd/
│   └── hyrule-network-proxy/
│       └── main.go
├── internal/
│   ├── config/
│   │   └── config.go
│   ├── server/
│   │   ├── server.go
│   │   ├── handlers.go
│   │   └── middleware.go
│   ├── contract/
│   │   └── types.go
│   ├── policy/
│   │   ├── url.go
│   │   ├── headers.go
│   │   ├── ip.go
│   │   └── redirect.go
│   ├── transport/
│   │   ├── client.go
│   │   ├── direct.go
│   │   ├── tor.go
│   │   ├── i2p.go
│   │   └── yggdrasil.go
│   ├── metrics/
│   │   └── metrics.go
│   └── version/
│       └── version.go
├── systemd/
│   └── hyrule-network-proxy.service
├── packaging/
│   └── env.example
└── tests/
    └── integration/
```

## Go Dependencies

Use mostly standard library.

Required dependencies:

```text
golang.org/x/net/proxy
github.com/prometheus/client_golang/prometheus
github.com/prometheus/client_golang/prometheus/promhttp
```

Do not use Gin/Echo/Fiber. Use `net/http`.

## Sidecar Runtime Config

Environment variables:

```env
HNP_API_LISTEN_ADDR=[2a0c:b641:b50:2::e0]:8450
HNP_METRICS_LISTEN_ADDR=[2a0c:b641:b50:2::e0]:8451
HNP_AUTH_TOKEN=<vault-secret>

HNP_TOR_SOCKS_ADDR=127.0.0.1:9050
HNP_I2P_HTTP_PROXY=http://127.0.0.1:4444
HNP_YGGDRASIL_ENABLED=true

HNP_MAX_REQUEST_BODY_BYTES=65536
HNP_MAX_RESPONSE_BODY_BYTES=65536
HNP_DEFAULT_TIMEOUT_SECONDS=15
HNP_MAX_TIMEOUT_SECONDS=60
HNP_MAX_REDIRECTS=3

HNP_LOG_LEVEL=info
```

Yggdrasil peers are configured by infra through the system `yggdrasil` daemon, not by the Go sidecar directly.

If Yggdrasil has no active peers/routes, sidecar reports:

```json
"yggdrasil": {
  "available": false,
  "reason": "no yggdrasil connectivity"
}
```

## Sidecar Routing Semantics

### `direct`

Allowed:

- `http`
- `https`
- public/global unicast destinations only

Denied:

- loopback
- RFC1918
- link-local
- multicast
- IPv6 ULA
- metadata IPs
- `.onion`
- `.i2p`
- Yggdrasil `200::/7` destinations

DNS-rebinding protection:

- resolve hostname inside the sidecar dialer;
- validate the exact IP selected;
- dial that validated IP.

### `tor`

Allowed:

- `.onion`
- clearnet public/global destinations

Transport:

- `.onion`: SOCKS5 to `127.0.0.1:9050` with remote name resolution.
- clearnet: resolve locally, validate public IP, then connect through Tor SOCKS to the selected IP.

Denied:

- private/link-local/metadata clearnet destinations
- `.i2p`
- Yggdrasil-only destinations

### `i2p`

Allowed:

- `.i2p` hostnames only

Transport:

- local `i2pd` HTTP proxy at `http://127.0.0.1:4444`

Denied:

- clearnet outproxy usage
- `.onion`
- non-`.i2p` destinations

### `yggdrasil`

Allowed:

- literal IPv6 addresses in `200::/7`
- hostnames that resolve exclusively to `200::/7`

Transport:

- normal system routing through the local Yggdrasil TUN interface.

Denied:

- clearnet public IPs
- `.onion`
- `.i2p`
- private/link-local/metadata destinations

## Header Policy

### Request headers forwarded to upstream

Allowlist only:

```text
accept
accept-language
cache-control
content-type
if-modified-since
if-none-match
user-agent
```

Drop all others by default, including:

```text
authorization
cookie
proxy-authorization
x-api-key
x-payment
payment-signature
```

The sidecar never receives x402 headers from Hyrule Cloud.

### Response headers returned to client

Return response headers except sensitive headers:

```text
authorization
cookie
proxy-authorization
set-cookie
x-api-key
x-payment
payment-signature
```

If response body is truncated, include in returned headers:

```json
"x-hyrule-truncated": "true"
```

## Body, Timeout, Redirect Policy

- Max request body: 64 KiB
- Max response body: 64 KiB
- Default timeout: 15s
- Max timeout: 60s
- Max redirects: 3
- Redirect target must be revalidated under the same proxy mode.
- Cross-network redirects are denied:
  - Tor `.onion` cannot redirect to I2P.
  - I2P cannot redirect to clearnet.
  - Yggdrasil cannot redirect to clearnet.
  - Direct cannot redirect to `.onion`, `.i2p`, or `200::/7`.

## Sidecar Observability

### Logs

Emit newline-delimited JSON to stdout:

```json
{
  "ts": "...",
  "level": "info",
  "service": "hyrule-network-proxy",
  "request_id": "...",
  "proxy_mode": "tor",
  "method": "GET",
  "target_host": "example.com",
  "status_code": 200,
  "elapsed_ms": 312,
  "truncated": false
}
```

Do not log:

- request body
- response body
- payment headers
- authorization headers
- cookies
- full query strings if avoidable

Log URL as:

- scheme
- host
- path only if needed
- no query string by default

### Metrics

Expose Prometheus text metrics on `:8451/metrics`.

Required metrics:

```text
hyrule_network_proxy_requests_total{mode,status_class}
hyrule_network_proxy_request_duration_seconds_bucket{mode}
hyrule_network_proxy_request_bytes_total{mode}
hyrule_network_proxy_response_bytes_total{mode}
hyrule_network_proxy_policy_denials_total{mode,reason}
hyrule_network_proxy_mode_available{mode}
```

### Health

Expose on `:8451`:

```text
GET /healthz
GET /readyz
GET /metrics
```

`/readyz` returns non-200 only if the service cannot process any mode.

Individual mode health is reported through authenticated `GET /v1/modes`.

## Hyrule Cloud Changes

### Config

Add to `HyruleConfig`:

```python
network_proxy_url: str = "http://127.0.0.1:8450"
network_proxy_token: str = ""
network_proxy_health_ttl_seconds: int = 15
```

Production env:

```env
HYRULE_NETWORK_PROXY_URL=http://[2a0c:b641:b50:2::e0]:8450
HYRULE_NETWORK_PROXY_TOKEN=<vault-secret>
HYRULE_NETWORK_PROXY_HEALTH_TTL_SECONDS=15
```

### Payment config

Replace:

```python
price_proxy_residential: Decimal = Decimal("0.20")
```

With:

```python
price_proxy_i2p: Decimal = Decimal("0.05")
price_proxy_yggdrasil: Decimal = Decimal("0.03")
```

Keep:

```python
price_proxy_direct: Decimal = Decimal("0.01")
price_proxy_tor: Decimal = Decimal("0.05")
```

### Pricing endpoint

Return:

```json
"proxy_prices": {
  "direct": "$0.01/request",
  "tor": "$0.05/request",
  "i2p": "$0.05/request",
  "yggdrasil": "$0.03/request"
}
```

### x402 manifest

Update resource description:

```text
Make a micro-proxy network request over Direct, Tor, I2P, or Yggdrasil
```

### Provider replacement

Replace current `NetworkProvider` internals with a sidecar client.

Responsibilities:

- validate method/body/url shape cheaply;
- cache `GET /v1/modes`;
- preflight mode availability before `PaymentGate.check_payment`;
- call `POST /v1/request` after payment;
- map sidecar auth/connect failures to Hyrule `502`.

Hyrule Cloud should no longer use local Tor SOCKS directly.

## Hyrule Infra Changes

### Inventory

Add host:

```yaml
netproxy:
  ansible_host: 2a0c:b641:b50:2::e0
```

Add to groups:

- `linux`
- `infra_vms`
- not `public_facing`

Add to `peers` in `group_vars/all.yml`:

```yaml
netproxy:
  ipv6: 2a0c:b641:b50:2::e0
```

### Host vars

Create:

```text
ansible/inventory/host_vars/netproxy.yml
```

Contents:

```yaml
---
# netproxy — internal Hyrule network proxy sidecar target.

hyrule_network_proxy_version: "<40-char-sha>"

firewall_extra_rules:
  - proto: tcp
    dport: 8450
    src: "{{ peers.api.ipv6 }}"
    comment: "hyrule-cloud API to network proxy sidecar"

  - proto: tcp
    dport: 8451
    src: "{{ peers.mon.ipv6 }}"
    comment: "Prometheus scrape hyrule-network-proxy metrics"

  - proto: tcp
    dport: 9100
    src: "{{ peers.mon.ipv6 }}"
    comment: "node_exporter scrape"

logs_register: true
logs_role: netproxy
```

### New Ansible role

Create:

```text
ansible/roles/hyrule_network_proxy/
```

Role responsibilities:

- install packages:
  - `ca-certificates`
  - `curl`
  - `git`
  - `golang` or deploy prebuilt binary
  - `tor`
  - `i2pd`
  - `yggdrasil`
  - `prometheus-node-exporter`
- create user/group:
  - `hyrule-netproxy`
- checkout/build/deploy pinned `hyrule_network_proxy_version`
- install binary to:
  - `/usr/local/bin/hyrule-network-proxy`
- render env file:
  - `/etc/hyrule-network-proxy/env`
- render systemd unit:
  - `/etc/systemd/system/hyrule-network-proxy.service`
- configure Tor client SOCKS on loopback only
- configure i2pd HTTP proxy on loopback only
- configure Yggdrasil daemon from inventory/Vault-provided peers
- restart on binary/config changes
- run health check after deploy

### Systemd unit

```ini
[Unit]
Description=Hyrule Network Proxy
After=network-online.target tor.service i2pd.service yggdrasil.service
Wants=network-online.target tor.service i2pd.service yggdrasil.service

[Service]
Type=exec
User=hyrule-netproxy
Group=hyrule-netproxy
EnvironmentFile=/etc/hyrule-network-proxy/env
ExecStart=/usr/local/bin/hyrule-network-proxy
Restart=always
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/hyrule-network-proxy
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hyrule-network-proxy

[Install]
WantedBy=multi-user.target
```

### Vault

Add secret key under `kv/data/hyrule-cloud`:

```text
network_proxy_token
```

Use the same value in:

- Hyrule Cloud env as `HYRULE_NETWORK_PROXY_TOKEN`
- netproxy env as `HNP_AUTH_TOKEN`

Add optional netproxy-specific Vault path if preferred by role:

```text
kv/data/hyrule-network-proxy
```

Required values:

```text
auth_token
yggdrasil_peers
```

### Hyrule Cloud env template

Update:

```text
configs/hyrule-cloud.env.j2
ansible/roles/vault_agent/templates/hyrule-cloud.env.ctmpl.j2
```

Add:

```env
HYRULE_NETWORK_PROXY_URL=http://[2a0c:b641:b50:2::e0]:8450
HYRULE_NETWORK_PROXY_TOKEN={{ network_proxy_token }}
HYRULE_NETWORK_PROXY_HEALTH_TTL_SECONDS=15

PAYMENT_PRICE_PROXY_DIRECT=0.01
PAYMENT_PRICE_PROXY_TOR=0.05
PAYMENT_PRICE_PROXY_I2P=0.05
PAYMENT_PRICE_PROXY_YGGDRASIL=0.03
```

Remove:

```env
PAYMENT_PRICE_PROXY_RESIDENTIAL
```

### Deployment pins

Add `hyrule_network_proxy_version` as a production-controlled app pin in infra.

Update deployment documentation and promotion automation so production sidecar version is pinned by a 40-character SHA, same pattern as existing app pins.

## New Repo CI

In `hyrule-network-proxy`, add GitHub Actions:

```yaml
go test ./...
go vet ./...
go test -race ./...
go build ./cmd/hyrule-network-proxy
```

Optional but recommended:

- `staticcheck`
- upload built binary artifact for deploy workflows

## Test Plan

### Go sidecar unit tests

Policy tests:

- rejects unsupported method
- rejects unsupported scheme
- rejects URL with no host
- rejects direct `.onion`
- rejects direct `.i2p`
- rejects direct Yggdrasil `200::/7`
- rejects direct private IPv4
- rejects direct loopback IPv4/IPv6
- rejects link-local
- rejects ULA IPv6
- allows public IPv4/IPv6
- allows `.onion` only in `tor`
- allows `.i2p` only in `i2p`
- allows `200::/7` only in `yggdrasil`

Header tests:

- forwards allowlisted request headers
- drops `authorization`
- drops `cookie`
- drops `x-payment`
- redacts `set-cookie` from response

Body tests:

- rejects request body over 64 KiB
- truncates response body over 64 KiB
- sets `x-hyrule-truncated=true`

Redirect tests:

- permits same-mode safe redirect
- rejects redirect to private IP
- rejects Tor-to-I2P redirect
- rejects I2P-to-clearnet redirect
- rejects Yggdrasil-to-clearnet redirect

Mode health tests:

- Tor unavailable if SOCKS port unavailable
- I2P unavailable if proxy unavailable
- Yggdrasil unavailable if no route/peer detected
- Direct available if normal dialer works

### Go sidecar integration tests

Use `httptest` for:

- direct successful GET
- direct POST echoes body
- timeout handling
- response truncation
- metrics increment

Use fake local proxy servers where practical for:

- I2P HTTP proxy behavior
- Tor SOCKS unavailable behavior

Full Tor/I2P/Yggdrasil live integration tests are optional/manual and should be skipped in CI unless env vars explicitly enable them.

### Hyrule Cloud tests

Update or add tests for:

- `/v1/pricing` includes `direct`, `tor`, `i2p`, `yggdrasil`
- `/v1/pricing` does not include `residential`
- `/.well-known/x402.json` advertises network request resource with new modes
- `POST /v1/network/request` without payment returns `402` for healthy mode
- unavailable mode returns `503` before payment
- sidecar auth/connect failure maps to `502`
- paid request calls sidecar exactly once
- `proxy_mode=residential` is rejected
- `proxy_mode=i2p` prices with `price_proxy_i2p`
- `proxy_mode=yggdrasil` prices with `price_proxy_yggdrasil`

### Hyrule Infra tests

Update render/static tests so:

- `netproxy` inventory is valid
- `netproxy` firewall allows `8450` only from `api`
- `netproxy` metrics allows `8451` only from `mon`
- `netproxy` has logs enabled
- `hyrule_network_proxy_version` must be 40-char SHA
- Hyrule Cloud env renders sidecar URL/token settings
- Vault template renders sidecar settings

### Smoke tests

Add smoke checks:

```bash
curl -6 -sf https://cloud.hyrule.host/.well-known/x402.json \
  | grep -q '/v1/network/request'

curl -6 -sf https://cloud.hyrule.host/v1/pricing \
  | grep -q 'yggdrasil'
```

Internal smoke from `api` VM:

```bash
curl -6 -sf \
  -H "Authorization: Bearer $HYRULE_NETWORK_PROXY_TOKEN" \
  http://[2a0c:b641:b50:2::e0]:8450/v1/modes
```

From `mon`:

```bash
curl -6 -sf http://[2a0c:b641:b50:2::e0]:8451/metrics
```

## Rollout Plan

### Stage 1: Sidecar repo

- Create `AS215932/hyrule-network-proxy`.
- Implement sidecar.
- Merge only after Go CI passes.
- Produce first deployable SHA.

### Stage 2: Hyrule Cloud integration

- Add sidecar client provider.
- Add new `ProxyMode` values.
- Remove advertised residential mode.
- Add config/env support.
- Add tests.
- Merge after Hyrule Cloud CI passes.

### Stage 3: Infra deployment

- Add `netproxy` VM to inventory.
- Add role/playbook/service/firewall/logging/monitoring.
- Add Vault token wiring.
- Pin `hyrule_network_proxy_version`.
- Deploy `netproxy`.
- Confirm sidecar `/v1/modes` from `api`.

### Stage 4: Hyrule Cloud production promotion

- Promote Hyrule Cloud SHA with sidecar client.
- Ensure `HYRULE_NETWORK_PROXY_URL` and token are rendered.
- Deploy to `api`.
- Run smoke tests.
- Confirm unpaid request returns `402` only for available modes.

### Stage 5: Public enablement

- Confirm `/v1/pricing` and x402 manifest advertise new modes.
- Start with direct + Tor + I2P available.
- Yggdrasil may report unavailable until peers are configured; Hyrule Cloud must return `503` before payment for unavailable mode.

## Acceptance Criteria

The work is complete when:

1. `hyrule-network-proxy` exists as a separate Go repo.
2. Sidecar builds and passes Go tests.
3. Sidecar exposes authenticated:
   - `GET /v1/health`
   - `GET /v1/modes`
   - `POST /v1/request`
4. Sidecar exposes unauthenticated, firewall-protected:
   - `GET /healthz`
   - `GET /readyz`
   - `GET /metrics`
5. Hyrule Cloud no longer performs network proxying directly with `httpx`/Tor SOCKS.
6. Hyrule Cloud delegates paid network requests to the sidecar.
7. Hyrule Cloud preflights sidecar mode availability before x402 payment.
8. Public API supports:
   - `direct`
   - `tor`
   - `i2p`
   - `yggdrasil`
9. Public API no longer advertises `residential`.
10. Infra deploys sidecar to `netproxy` VM.
11. Firewall restricts sidecar API port `8450` to `api`.
12. Metrics port `8451` is reachable from `mon`.
13. Logs ship through existing Vector/Loki flow.
14. Smoke tests confirm x402 manifest, pricing, sidecar health, and sidecar metrics.
15. No payment headers, auth headers, cookies, request bodies, or response bodies are logged.

## Assumptions

- `netproxy` will use IPv6 `2a0c:b641:b50:2::e0`.
- `netproxy.servify.network` is infrastructure identity and should not be customer-facing.
- `hyrule.host` remains customer-facing API/product identity.
- `servify.network` is correct for internal hostnames.
- Yggdrasil peer addresses are deployment config, not hardcoded in Go.
- If Yggdrasil is not connected, the mode remains visible only if Hyrule Cloud chooses to advertise it, but requests return `503` before payment.
- No residential proxy product is built or advertised.
- MVP returns text bodies using UTF-8 replacement for invalid bytes; binary-object proxying is out of scope.
- No streaming support in MVP.
- No CONNECT tunneling in MVP.
- No per-wallet rate limiting in this plan; it can be added later using Hyrule Cloud account/payment identity.
