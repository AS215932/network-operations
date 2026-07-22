# x402 domain-sales rollout

This runbook launches one-year, wallet-owned domain registration through
`POST /v1/domains/registrations` and the same-origin Hyrule web proxy. BTC,
Monero, renewals, DNS changes, and transfers remain account-scoped.

## Safety invariants

- Legal and tax approval must be recorded before enabling purchases.
- The registrar credentials, contact handles, managed-DNS control secret, and
  fresh eligible-TLD catalog must already be healthy.
- Canary scope is `.xyz` plus two explicit payer wallets.
- Each launch purchase has a hard `$10.00` ceiling. The two-purchase acceptance
  run therefore cannot authorize more than `$20.00` total.
- Never reuse a canary label or `client_order_id` after an ambiguous response;
  poll its public status URL and let settlement recovery converge.
- Production app pins are changed only by `promote-apps`; the final apply still
  requires approval of the `production` environment.

## 1. Converge authoritative DNS

Export the existing `TSIG_SECRET` and `HYRULE_DNS_CONTROL_SECRET`, then run the
gated Knot play for `dns` followed by `ns2`:

```sh
ansible-playbook ansible/playbooks/knot.yml --tags apply --limit dns,ns2
```

The nameserver inventory has `knot_apply: true`, so this installs and starts
`hyrule-dns-control.service`, enables `knot-online-backup.timer`, distributes
`hyrule.host.zone`, and transfers it to ns2. The apex delegation intentionally
remains `ns1.servify.network` / `ns2.servify.network`; only the branded
`ns1.hyrule.host` / `ns2.hyrule.host` host records are published for customer zones.

Verify from outside AS215932:

```sh
dig @46.105.40.223 hyrule.host SOA +short
dig @46.105.40.223 ns1.hyrule.host A +short
dig @54.38.14.218 ns2.hyrule.host A +short
dig hyrule.host NS +short
```

Also confirm the public IPv4 Prometheus targets for both `46.105.40.223:53` and
`54.38.14.218:53` are green, and on the primary:

```sh
systemctl is-active hyrule-dns-control.service knot.service
systemctl is-enabled knot-online-backup.timer
```

## 2. Canary configuration

After the cloud and web changes are merged, promote their exact merged SHAs.
Set these fields in `kv/hyrule-cloud` before approving the production apply:

```text
domain_purchases_enabled=true
domain_legal_approved=true
domain_tax_approved=true
domain_terms_version=2026-07-19
domain_marketplace_sales_enabled=true
domain_tld_allowlist=["xyz"]
domain_allow_all_eligible_tlds=false
domain_marketplace_payer_allowlist=["0x<direct-canary-wallet>","0x<web-canary-wallet>"]
domain_registration_limit_per_24h=5
domain_marketplace_preflights_per_hour=60
```

Keep the payer wallets distinct and fund each only for its capped purchase plus
gas. With a non-empty payer allowlist the endpoint works for the cohort but is
intentionally absent from the public x402 manifest and curated OpenAPI.

Before spending, require all of the following:

```sh
curl -fsS https://cloud.hyrule.host/v1/domains/sales/status
curl -fsS https://cloud.hyrule.host/v1/domains/tlds
curl -fsS https://cloud.hyrule.host/.well-known/x402.json
```

The sales status must be enabled, `.xyz` must be eligible and fresh, and the
manifest must not yet list `/v1/domains/registrations`.

## 3. Two real acceptance purchases

Use a unique DNS-safe base label. The canary creates `<label>-api.xyz` directly
and `<label>-web.xyz` through `https://hyrule.host/api/...`:

```sh
export CANARY_KEY_API=0x<direct-wallet-private-key>
export CANARY_KEY_WEB=0x<web-wallet-private-key>
python scripts/x402_canary.py domain --name hyrule-launch-20260719 --yes
```

The canary refuses any quote above `$10.00`, passes the reviewed quote total as
`max_price_usd`, enforces an independent `$10.00` signer policy, requires a
successful settlement header, and polls each opaque public status URL until the
order is active. Abort the rollout on a cap refusal, missing
settlement header, ownership mismatch, `refund_due`, or non-convergent status.
Do not buy a replacement for an ambiguous order.

Separately open the web checkout for the web canary quote and confirm the page
shows wallet-owned USDC checkout without login, while BTC and Monero remain
login-only. Confirm the paid response creates a management session and the
domain appears in the dashboard.

## 4. Public launch

After both purchases are active and authoritative on both nameservers, change
only the cohort/TLD scope in Vault:

```text
domain_tld_allowlist=[]
domain_allow_all_eligible_tlds=true
domain_marketplace_payer_allowlist=[]
```

Approve the normal production apply. Then verify the registration route is in
both `/.well-known/x402.json` and `/openapi.json`, its advertised dynamic floor
is `$3.00`, an unpaid valid request receives an exact 402, and the web checkout
still sends its reviewed quote total as `max_price_usd`.

## Rollback

Set `domain_marketplace_sales_enabled=false` and apply. This immediately removes
public discovery and fails new marketplace checkouts closed; it does not alter
settled orders, customer zones, account checkout, registrar state, or the
intentional `servify.network` apex delegation. Keep workers, DNS control, and
settlement recovery running until every in-flight intent is terminal.
