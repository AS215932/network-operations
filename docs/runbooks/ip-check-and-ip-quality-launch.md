# IP check and IP-quality launch

The network-check session is agent-first. The Python SDK or MCP client runs
HTTPS, DNS, and RFC 5389 STUN probes in the agent process; the browser page is
an optional adapter that adds WebRTC and browser-fingerprint evidence.

All launch controls default dark:

- `IP_CHECK_ENABLED=false` hides the API session surface.
- `HYRULE_WEB_ENABLE_IP_CHECK=false` hides the browser adapter.
- `IP_QUALITY_ENABLED=false` and `HYRULE_IP_QUALITY_TOOL_ENABLED=false` keep
  the paid report out of OpenAPI, x402 discovery, Bazaar, and MCP.

## 1. Stage the shared DNS-observer secret

Generate one value and store it in `kv/hyrule-cloud`:

```bash
observer_secret="$(openssl rand -hex 32)"
vault kv patch kv/hyrule-cloud ip_check_dns_observer_secret="$observer_secret"
```

Export the same value only for the Knot apply. It signs a timestamp and the
exact JSON body; it is never sent to a browser or agent.

## 2. Deploy the dark observer plane

Review each check-mode diff, then apply in this order:

```bash
cd ansible

ansible-playbook playbooks/firewall.yml --tags apply \
  -e firewall_apply=true --limit 'rtr:proxy'

TSIG_SECRET='...' \
HYRULE_DNS_CONTROL_SECRET='...' \
HYRULE_IP_CHECK_DNS_OBSERVER_SECRET="$observer_secret" \
ansible-playbook playbooks/knot.yml --tags apply \
  -e knot_apply=true --limit nameservers

TSIG_SECRET='...' \
ansible-playbook playbooks/ip-check-observer.yml --tags apply \
  -e ip_check_observer_apply=true --limit proxy

ansible-playbook playbooks/icinga2.yml --tags apply \
  -e icinga2_apply=true --limit mon
```

The Knot module is attached only to `dns.check.hyrule.host`; random session
labels return NXDOMAIN and are retained by Hyrule for at most 15 minutes.
coturn runs with `stun-only`, `no-auth`, and no TLS, DTLS, CLI, relay, or
binding logging. Icinga performs real IPv4 and IPv6 binding requests.

## 3. Canary the agent path

Deploy the matching Hyrule Cloud release, set only `ip_check_enabled=true` in
Vault, and leave the web flag false. Run the SDK's
`network_environment_check()` from at least one IPv4-only and one dual-stack
agent. Confirm:

- the HTTPS observations contain the agent's egress addresses, not Caddy's;
- the unique DNS label produces only resolver evidence for its live session;
- STUN failure is reported as inconclusive, not as a leak;
- the report and session token are rejected after 15 minutes;
- no access log contains bearer tokens or fingerprint payloads.

Then enable the browser adapter on a small web canary. High-entropy WebGL,
canvas, and audio traits must remain behind explicit consent.

## 4. Launch licensed IP quality separately

Do not enable the paid route until written MaxMind and IPQS resale approval is
recorded. Configure both credentials, approved per-request costs, and keep the
combined provider cost at or below 40% of `PAYMENT_PRICE_IP_QUALITY`. Caching
stays off unless the contracts explicitly grant caching rights.

Enable in order: API allowlisted canary, paid-report reconciliation, MCP tool,
then general discovery. A provider error must return a retryable service error
without settling the x402 payment.

## Rollback

Turn off the web flag, `IP_CHECK_ENABLED`, `HYRULE_IP_QUALITY_TOOL_ENABLED`, and
`IP_QUALITY_ENABLED`. These switches remove customer discovery without
requiring an emergency DNS or firewall rollback. Stop coturn or the DNS
observer only if the observer plane itself is unhealthy.
