# Bootstrap: Hyrule Cloud Vault AppRole

Hyrule Cloud runtime secrets are rendered on the `api` VM by
`vault-agent-hyrule-cloud.service`. The GitHub runner must not render or source
`XO_TOKEN`.

## 1. Write the policy

```bash
vault policy write hyrule-cloud configs/vault/policies/hyrule-cloud.hcl
```

## 2. Create the AppRole

```bash
vault write auth/approle/role/hyrule-cloud \
    token_policies="hyrule-cloud" \
    token_ttl=30m \
    token_max_ttl=4h \
    secret_id_ttl=10m \
    secret_id_num_uses=1
```

Use response wrapping for bootstrap/re-bootstrap. Vault Agent expects the
wrapped token file to have creation path
`auth/approle/role/hyrule-cloud/secret-id`.

## 3. Populate the KV entry

Create `payment-networks.json` from the reviewed production catalog. Solana is
mainnet-only and uses the canonical USDC mint; the API still confirms CDP's
live `/supported` response before advertising any entry.

```json
[
  {
    "key": "base",
    "display_name": "Base",
    "caip2": "eip155:8453",
    "family": "evm",
    "chain_id": 8453,
    "asset": "USDC",
    "token_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "token_decimals": 6,
    "eip712_domain": {"name": "USD Coin", "version": "2"},
    "native_currency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
    "rpc_url": "https://mainnet.base.org",
    "block_explorer_url": "https://basescan.org",
    "enabled": true
  },
  {
    "key": "polygon",
    "display_name": "Polygon",
    "caip2": "eip155:137",
    "family": "evm",
    "chain_id": 137,
    "asset": "USDC",
    "token_address": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    "token_decimals": 6,
    "eip712_domain": {"name": "USD Coin", "version": "2"},
    "native_currency": {"name": "POL", "symbol": "POL", "decimals": 18},
    "rpc_url": "https://polygon-rpc.com",
    "block_explorer_url": "https://polygonscan.com",
    "enabled": true
  },
  {
    "key": "arbitrum",
    "display_name": "Arbitrum",
    "caip2": "eip155:42161",
    "family": "evm",
    "chain_id": 42161,
    "asset": "USDC",
    "token_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "token_decimals": 6,
    "eip712_domain": {"name": "USD Coin", "version": "2"},
    "native_currency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
    "rpc_url": "https://arb1.arbitrum.io/rpc",
    "block_explorer_url": "https://arbiscan.io",
    "enabled": true
  },
  {
    "key": "solana",
    "display_name": "Solana",
    "caip2": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
    "family": "svm",
    "chain_id": null,
    "asset": "USDC",
    "token_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "token_decimals": 6,
    "native_currency": {"name": "Solana", "symbol": "SOL", "decimals": 9},
    "rpc_url": "https://api.mainnet-beta.solana.com",
    "block_explorer_url": "https://explorer.solana.com",
    "wallet_chain": "solana:mainnet",
    "enabled": true
  }
]
```

The receiver map is explicit per network. EVM entries may share one address;
Solana must use the reviewed mainnet receiving account. The CDP secret is the
ES256 private-key PEM downloaded with the API key and must never be committed.

```bash
vault kv put kv/hyrule-cloud \
    xo_token="..." \
    sr_uuid="..." \
    vm_network_uuid="..." \
    xcpng_templates='{"debian-13":"..."}' \
    openprovider_username="..." \
    openprovider_password="..." \
    openprovider_owner_handle="..." \
    openprovider_admin_handle="..." \
    openprovider_tech_handle="..." \
    openprovider_billing_handle="..." \
    openprovider_nameservers='["ns1.openprovider.nl","ns2.openprovider.be","ns3.openprovider.eu"]' \
    payment_wallet="0x..." \
    payment_receiver_addresses='{"base":"0x...","polygon":"0x...","arbitrum":"0x...","solana":"SOLANA_MAINNET_ADDRESS"}' \
    payment_networks="$(jq -c . payment-networks.json)" \
    payment_facilitator_url="https://api.cdp.coinbase.com/platform/v2/x402" \
    cdp_api_key_id="organizations/.../apiKeys/..." \
    cdp_api_key_secret="$(cat /secure/path/cdp-api-key.pem)" \
    btc_xpub="xpub-or-zpub..." \
    payment_native_assets_enabled='["BTC"]' \
    xmr_viewkey="..." \
    xmr_wallet_address="..." \
    xmr_wallet_password="..." \
    xmr_restore_height="0" \
    xmr_daemon_address="node.moneroworld.com:18089" \
    xmr_rpc_url="http://127.0.0.1:18088/json_rpc" \
    ip_prefix_pepper="$(openssl rand -hex 32)" \
    dev_bypass_secret="" \
    tsig_secret="..." \
    db_password="..." \
    network_proxy_token="..."
```

`btc_xpub` is required in production. It must be a public receive-only extended
key; never place a seed phrase or private extended key in this KV entry.

Optional OpenBSD builder keys:

```bash
vault kv patch kv/hyrule-cloud \
    xcpng_openbsd_builder_vm_uuid="..." \
    xcpng_openbsd_builder_ssh_host="..." \
    xcpng_openbsd_builder_ssh_user="svag"
```

## 4. Bootstrap or re-bootstrap the api VM

```bash
export VAULT_HYRULE_CLOUD_ROLE_ID="$(
  vault read -field=role_id auth/approle/role/hyrule-cloud/role-id
)"
export VAULT_HYRULE_CLOUD_WRAPPED_SECRET_ID="$(
  vault write -wrap-ttl=60s -f auth/approle/role/hyrule-cloud/secret-id \
    | awk '/wrapping_token:/ {print $2}'
)"

cd ansible
ansible-playbook playbooks/cloud.yml --tags apply \
  -e hyrule_cloud_apply=true \
  -e hyrule_cloud_version=<sha-or-ref> \
  --limit api
```

The wrapped SecretID is single-use and short-lived. If the first apply misses
the 60-second wrapping window, mint a fresh wrapped SecretID and retry.

## Verify

```bash
ssh root@2a0c:b641:b50:2::20 systemctl status vault-agent-hyrule-cloud
ssh root@2a0c:b641:b50:2::20 'ls -l /opt/hyrule-cloud/.env'   # root:hyrule 0640
ssh root@2a0c:b641:b50:2::20 'ls -l /etc/hyrule-cloud/monero-wallet-rpc.env'  # root:hyrule 0640
ssh root@2a0c:b641:b50:2::20 systemctl status monero-wallet-rpc
ssh root@2a0c:b641:b50:2::20 systemctl status hyrule-cloud hyrule-cloud-worker
ssh root@2a0c:b641:b50:2::20 \
  'curl -fsS http://[::1]:8402/v1/payments/networks | jq {networks,native,native_worker}'
```

The catalog must contain `solana`, list `BTC`, and report
`native_worker.ready: true`. The Ansible deploy now retries this same gate and
fails the promotion if CDP support or the worker heartbeat is absent.
Complete the bounded paid matrix in
[`payment-rails-canary.md`](./payment-rails-canary.md) before declaring the
rails promoted.

Secret rotations in `kv/hyrule-cloud` cause Vault Agent to re-render
`/opt/hyrule-cloud/.env` and `/etc/hyrule-cloud/monero-wallet-rpc.env`; the
render hooks validate required keys and restart the affected services.
