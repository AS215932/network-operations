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
    btc_xpub="xpub-or-zpub..." \
    xmr_viewkey="..." \
    xmr_wallet_address="..." \
    xmr_wallet_password="..." \
    xmr_restore_height="0" \
    xmr_daemon_address="node.moneroworld.com:18089" \
    xmr_rpc_url="http://127.0.0.1:18088/json_rpc" \
    ip_prefix_pepper="$(openssl rand -hex 32)" \
    dev_bypass_secret="" \
    tsig_secret="..." \
    db_password="..."
```

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
ssh root@2a0c:b641:b50:2::20 systemctl status hyrule-cloud
```

Secret rotations in `kv/hyrule-cloud` cause Vault Agent to re-render
`/opt/hyrule-cloud/.env` and `/etc/hyrule-cloud/monero-wallet-rpc.env`; the
render hooks validate required keys and restart the affected services.
