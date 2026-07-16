# Managed domain and DNS rollout

This runbook covers the `hyrule.host` domain-reselling path: the public/API
application on `cloud.hyrule.host`, OpenProvider as registrar, and Hyrule's
Knot primary/secondary pair as the optional managed DNS service.

The existing `hyrule.host` zone remains delegated to
`ns1.servify.network`/`ns2.servify.network`. Customer zones use the branded
`ns1.hyrule.host`/`ns2.hyrule.host` names. Do not replace the apex SOA or NS of
`hyrule.host` while rolling this out.

## Safety model

- Domain search and quotes may be public. Purchase remains disabled unless
  `DOMAIN_PURCHASES_ENABLED`, `DOMAIN_LEGAL_APPROVED`, and
  `DOMAIN_TAX_APPROVED` are all true.
- Start with both a TLD allowlist and an account allowlist. Expand them only
  after a paid canary has completed registration, DNSSEC publication, renewal
  state refresh, and transfer-secret handling.
- The API process does not run lifecycle schedulers. Exactly one
  `hyrule-cloud-worker.service` consumes durable jobs.
- OpenProvider autorenew stays off. Hyrule renewal orders are explicit and the
  customer-facing renewal state is refreshed by the worker.
- OpenProvider is the registrar, not the DNS host. Managed zones go through the
  HMAC-authenticated service on the primary at TCP/8453.

## Preconditions

1. Confirm the branded host records are public from both address families:

   ```console
   dig +short A ns1.hyrule.host
   dig +short AAAA ns1.hyrule.host
   dig +short A ns2.hyrule.host
   dig +short AAAA ns2.hyrule.host
   ```

   Expected values are `46.105.40.223`, `2a0c:b641:b50:2::10`,
   `54.38.14.218`, and `2001:41d0:304:300::7bfb`, respectively. Where the
   parent registrar requires glue, confirm the same addresses there.

2. Put the following values in `kv/hyrule-cloud` before deploying the app:

   - OpenProvider credentials and all four contact handles
   - `domain_dns_control_secret` (at least 32 random characters; the identical
     value must be exported as `HYRULE_DNS_CONTROL_SECRET` for the Knot deploy)
   - `domain_authcode_fernet_key` (a Fernet key)
   - `domain_openprovider_webhook_secret`
   - explicit `domain_tld_allowlist` and `domain_account_allowlist` JSON arrays

3. Leave `domain_purchases_enabled`, `domain_legal_approved`, and
   `domain_tax_approved` false until the corresponding approvals exist.

4. Run the repository gate:

   ```console
   scripts/ci/iac-static.sh
   ```

## Deployment order

Deploy DNS before the application so a paid registration can never point at an
absent control plane.

1. Render and review the primary and secondary Knot configuration:

   ```console
   cd ansible
   TSIG_SECRET='<existing-tsig-secret>' \
   HYRULE_DNS_CONTROL_SECRET='<shared-control-secret>' \
   ansible-playbook playbooks/knot.yml --tags validate
   ```

2. Apply Knot to the primary and then the off-net secondary (`serial: 1` in the
   playbook enforces this ordering):

   ```console
   cd ansible
   TSIG_SECRET='<existing-tsig-secret>' \
   HYRULE_DNS_CONTROL_SECRET='<shared-control-secret>' \
   ansible-playbook playbooks/knot.yml --tags apply \
     -e knot_apply=true
   ```

3. Promote a pinned `hyrule-cloud` commit through the normal app-promotion PR,
   then approve its production deployment. The cloud role runs Alembic before
   restarting both the API and the dedicated worker.

4. Keep purchases disabled and verify the read-only surface first:

   ```console
   curl -fsS https://cloud.hyrule.host/health
   curl -fsS 'https://cloud.hyrule.host/v1/domains/check?domain=example.dev'
   systemctl --no-pager --full status hyrule-cloud hyrule-cloud-worker
   systemctl --no-pager --full status knot hyrule-dns-control knot-online-backup.timer
   ```

5. After legal and tax approval, enable the three launch flags for one
   allowlisted account and one or more explicitly allowlisted TLDs. Render the
   Vault template and restart the API/worker through the standard deploy path;
   do not edit `/opt/hyrule-cloud/.env` by hand.

## Paid canary acceptance

For an allowlisted account, complete one registration using managed
nameservers. Record the order and operation IDs, then verify:

```console
dig +short NS <canary-domain> @ns1.hyrule.host
dig +short NS <canary-domain> @ns2.hyrule.host
dig +dnssec DNSKEY <canary-domain> @ns1.hyrule.host
dig +dnssec DNSKEY <canary-domain> @ns2.hyrule.host
dig +dnssec DS <canary-domain>
```

Acceptance requires both branded NS answers, matching authoritative data on
both servers, DNSKEYs on both servers, a parent DS after registrar propagation,
an active domain order, and no raw provider error or auth code in API/log
output. Also exercise an external-nameserver transition on a separate canary
and confirm the old managed zone disappears from both servers.

## Operations and recovery

Useful diagnostics:

```console
journalctl -u hyrule-cloud-worker -u hyrule-cloud --since -1h
journalctl -u hyrule-dns-control -u knot --since -1h
knotc -c /etc/knot/knot.conf catalog-print customer-zones.catalog.invalid
systemctl list-timers knot-online-backup.timer
```

On worker startup, inspect the `recovered_bundle_vms` field. Paid bundle VMs
left in `provisioning` are restarted from their durable domain-order link. If
XO contains an exact `hyrule-<vm_id>` clone whose UUID was not committed before
the crash, the provisioner deletes that untracked candidate and recreates it;
do not submit a second bundle order to recover it manually.

The DNS control state is
`/var/lib/knot/hyrule-dns-control/state.json`. Each mutation is journaled there
before Knot is changed. A failed create, update, or deletion remains under
`pending` and is replayed when `hyrule-dns-control` starts or handles its next
request. Do not hand-edit the state or generated
`/var/lib/knot/customer-zones.conf`. If replay repeatedly fails, keep purchases
disabled, preserve the state file and journal, and fix the underlying Knot
error before restarting the service.

Daily online backups are written under `/var/backups/knot` and retain zone
files, journals, timers, catalog data, KASP/DNSSEC material, plus a copy of the
DNS-control state. Before a restore, disable purchases, stop
`hyrule-dns-control`, and copy the selected backup off-host. Restore the Knot
snapshot with the installed Knot version's `zone-restore` procedure, restore
`hyrule-dns-control-state.json` as `state.json` with owner `knot:knot` and mode
`0600`, then start Knot and the control service. Re-run the paid-canary DNS
checks before re-enabling purchases.

## Rollback

- Application: promote the prior pinned `hyrule_cloud_version`; never reset the
  live checkout manually. Alembic migrations are additive and should not be
  downgraded during an incident.
- DNS configuration: apply the prior reviewed network-operations commit. Do not
  delete customer zone state, zone files, catalog data, or KASP keys as part of
  a config rollback.
- Commercial stop: set `domain_purchases_enabled=false`. This blocks new sales
  while leaving existing domain reads, DNS management, reconciliation, and
  operational recovery available.
