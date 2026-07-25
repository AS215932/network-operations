# Bootstrap: Hyrule Cloud Vault AppRole

Hyrule Cloud runtime secrets are rendered on the `api` VM by
`vault-agent-hyrule-cloud.service`. The GitHub runner must not render or source
`XO_TOKEN`.

> **The single-use SecretID shape this runbook used to prescribe caused a
> week-long silent outage (2026-07-18 → 2026-07-25).** Vault Agent deletes a
> response-wrapped SecretID after reading it, so the next restart had no
> credential and 403-looped while `/opt/hyrule-cloud/.env` stayed frozen. The
> AppRole below is now durable and CIDR-bound. Background, alerting and recovery:
> [vault-agent-durable-auth.md](vault-agent-durable-auth.md).

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
    secret_id_ttl=0 \
    secret_id_num_uses=0 \
    secret_id_bound_cidrs="2a0c:b641:b50:2::20/128" \
    token_bound_cidrs="2a0c:b641:b50:2::20/128"
```

`secret_id_ttl=0` + `secret_id_num_uses=0` keep the SecretID readable on every
agent start, so a reboot or package upgrade does not take secret delivery down.
The `/128` CIDR bindings are what make that safe: the credential and the token
it mints are only usable from `api` itself. Do not widen them to the infra `/64`
— that would cover every other infra VM.

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

Optional native BTC payment key, only needed when native BTC is enabled:

```bash
vault kv patch kv/hyrule-cloud btc_xpub="xpub-or-zpub..."
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
export VAULT_HYRULE_CLOUD_SECRET_ID="$(
  vault write -f -field=secret_id auth/approle/role/hyrule-cloud/secret-id
)"
unset VAULT_HYRULE_CLOUD_WRAPPED_SECRET_ID

cd ansible
ansible-playbook playbooks/cloud.yml --tags apply \
  -e hyrule_cloud_apply=true \
  -e hyrule_cloud_version=<sha-or-ref> \
  --limit api
```

The role runs this consumer with
`hyrule_cloud_vault_require_durable_secret_id: true`, so it refuses a
response-wrapping token and writes the SecretID to
`/etc/vault-agent.d/hyrule-cloud-secret-id` (0600 root) to be re-read on every
start. If the apply fails with "needs role_id and secret_id bootstrap values",
the previous single-use credential has already been consumed — mint a durable
one as above and re-run.

## Verify

```bash
ssh root@2a0c:b641:b50:2::20 systemctl status vault-agent-hyrule-cloud
ssh root@2a0c:b641:b50:2::20 'ls -l /run/vault-agent/hyrule-cloud.token'  # exists
ssh root@2a0c:b641:b50:2::20 'ls -l /opt/hyrule-cloud/.env'   # root:hyrule 0640
ssh root@2a0c:b641:b50:2::20 'ls -l /etc/hyrule-cloud/monero-wallet-rpc.env'  # root:hyrule 0640
ssh root@2a0c:b641:b50:2::20 systemctl status monero-wallet-rpc
ssh root@2a0c:b641:b50:2::20 systemctl status hyrule-cloud

# The credential must survive a restart — this is the check the July 2026
# outage failed. The token sink must come back.
ssh root@2a0c:b641:b50:2::20 systemctl restart vault-agent-hyrule-cloud
ssh root@2a0c:b641:b50:2::20 'ls -l /run/vault-agent/hyrule-cloud.token'
```

Secret rotations in `kv/hyrule-cloud` cause Vault Agent to re-render
`/opt/hyrule-cloud/.env` and `/etc/hyrule-cloud/monero-wallet-rpc.env`; the
render hooks validate required keys and restart the affected services.
