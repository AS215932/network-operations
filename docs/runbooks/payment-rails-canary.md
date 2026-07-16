# Production payment-rails canary

Use this runbook to promote or re-validate x402 on EVM and Solana plus native
BTC payment intents. All rails are mainnet. Use operator-controlled wallets,
the cheapest enabled diagnostic for x402, and the one-day XS quote for BTC.
Never paste wallet secrets, seed phrases, CDP private keys, or management tokens
into tickets or CI logs.

## Preconditions

- The cloud API, web frontend, and infra revisions under test are SHA-pinned.
- `kv/hyrule-cloud` contains the reviewed network catalog, per-network receiver
  map, CDP credentials, receive-only BTC xpub, and
  `payment_native_assets_enabled='["BTC"]'`.
- The EVM, Solana, and BTC test wallets are funded for the bounded canary.
- An operator is watching Prometheus alerts and both
  `hyrule-cloud.service` and `hyrule-cloud-worker.service` logs.
- The pre-promotion SHAs and the current Vault payment values are recorded for
  rollback.

## 1. Prove advertised readiness

On the API VM:

```bash
catalog="$(curl -fsS http://[::1]:8402/v1/payments/networks)"
jq '{networks: [.networks[] | {key,caip2,family,asset,pay_to}], native, native_worker}' \
  <<<"$catalog"
jq -e '
  ([.networks[].key] | index("solana")) != null and
  ([.native[]] | index("BTC")) != null and
  .native_worker.ready == true
' <<<"$catalog"
```

Confirm the Solana entry uses:

- CAIP-2: `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`
- USDC mint: `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`
- Wallet chain: `solana:mainnet`
- The reviewed Solana receiver account

The catalog route initializes the x402 server and filters its output through
CDP's live supported-kind response. An absent Solana entry is a failed canary,
even if the static Vault JSON enables it.

## 2. Paid x402 matrix

Use the cheapest enabled toolbox diagnostic and retain only non-secret
evidence: UTC timestamp, resource path, selected network, amount, facilitator
transaction ID, and HTTP status.

1. Pay once with USDC on an enabled EVM chain. Confirm the response succeeds,
   the `Payment-Response` network matches the chosen CAIP-2 identifier, and the
   transaction is visible on that chain's explorer.
2. Repeat the same operation from `/toolbox` with **Solana** selected. Connect a
   Wallet Standard wallet, approve the exact transaction, and confirm the paid
   request succeeds. Verify the transaction on Solana Explorer and confirm the
   token recipient and amount match the 402 acceptance.
3. Fetch `/v1/payments/networks` again. Both tested networks must remain
   advertised and no `HyruleSolanaPaymentFailure` alert may be firing.

Do not accept a browser success screen alone. The API response, facilitator
receipt, and public-chain transaction must all agree on network, receiver,
asset, and base-unit amount.

## 3. BTC intent canary

Create a durable quote using a unique client order ID:

```bash
base=https://cloud.hyrule.host
order='{
  "duration_days": 1,
  "size": "xs",
  "os": "debian-13",
  "ssh_pubkey": "ssh-ed25519 REPLACE_WITH_CANARY_PUBLIC_KEY",
  "domain_mode": "auto",
  "open_ports": [22,80,443]
}'

quote="$(curl -fsS -X POST "$base/v1/vm/quote" \
  -H 'Content-Type: application/json' \
  --data "$(jq -nc --argjson order "$order" '{order_payload:$order}')")"
quote_id="$(jq -r .quote_id <<<"$quote")"

intent="$(curl -fsS -X POST "$base/v1/intent/create" \
  -H 'Content-Type: application/json' \
  --data "$(jq -nc \
    --arg asset BTC \
    --arg quote_id "$quote_id" \
    --arg client_order_id "btc-canary-$(date -u +%Y%m%dT%H%M%SZ)" \
    --argjson order "$order" \
    '{asset:$asset,client_order_id:$client_order_id,
      order_payload:($order + {quote_id:$quote_id})}')")"
jq '{intent_id,address,amount_crypto,expires_at,status}' <<<"$intent"
```

Send the exact BTC amount to the returned unique mainnet address. Poll the
intent without printing a management token:

```bash
intent_id="$(jq -r .intent_id <<<"$intent")"
while true; do
  status="$(curl -fsS "$base/v1/intent/$intent_id")"
  jq '{intent_id,status,confirmations,amount_received_crypto,vm_id}' <<<"$status"
  case "$(jq -r .status <<<"$status")" in
    PROVISIONED|FAILED|REFUND_MANUAL|EXPIRED) break ;;
  esac
  sleep 15
done
```

Acceptance requires a fresh worker heartbeat throughout, an on-chain scan
within 90 seconds, the expected confirmation transition, and eventual
`PROVISIONED`. Store any one-shot management token only in the approved secret
store.

## 4. Monitoring acceptance

Confirm all of the following in Prometheus:

```promql
hyrule_payment_worker_ready == 1
hyrule_payment_worker_last_success_age_seconds < 45
hyrule_native_payment_intents_pending{asset="BTC"}
hyrule_native_payment_intent_scan_lag_seconds{asset="BTC"} < 90
```

Also confirm no new `settle_failed`, `refund_owed`, or Solana payment-failure
event appeared during the canary window.

## Kill switches and rollback

- **Solana:** set its `enabled` field to `false` in the Vault
  `payment_networks` JSON. Vault Agent restarts API and worker; confirm Solana
  disappears from the catalog before investigating.
- **New BTC intents:** set `payment_native_assets_enabled='[]'`. Existing
  intents continue to be scanned, but the API stops advertising or creating
  new BTC intents. Do not remove the xpub while an intent is pending.
- **Code regression:** promote the previous pinned cloud/web/infra SHAs through
  the normal rollback PR and production approval path.

After any kill switch, re-run step 1 and attach the redacted catalog plus alert
timeline to the incident or launch record.
