# VPS launch-proof smoke

End-to-end proof of the AS215932 VPS launch-proof wedge: a customer can quote a
VM, be told payment is required, pay, watch it provision, and reach a working
box — or get a safe failure with rollback available. This runbook is the
human procedure; `scripts/smoke/vps-launch-proof.sh` automates the
no-payment-required checks and the status-contract assertions.

Identities (do not conflate): product = `hyrule.host`, infra = `servify.network`,
AS/routing = `as215932.net`.

## Preconditions

- The launch-proof contract (`AS215932/hyrule-cloud#29`) is **deployed** to the
  running cloud app — i.e. `hyrule_cloud_version` in `ansible/inventory/host_vars/`
  has been **promoted** to a SHA that includes #29 and applied (see Promotion below).
- A controlled **test order** and **test wallet / payment path** for the paid leg.
- Default is controlled simulation; real XCP-NG/Openprovider/DNS are opt-in on the
  cloud side via `HCP_LAUNCH_PROOF_REAL_XCPNG=1`.

## Promotion (deploy #29's contract — do this first)

Use the app-promotion path, never a manual pin edit:

1. `gh workflow run promote-apps.yml -F hyrule_cloud_sha=<#29 merge SHA> -F note="VPS launch-proof contract"`
2. Review + merge the promotion PR.
3. `app-promotion-deploy` calls `apply.yml playbook=cloud`; approve the `production` gate.
4. Record the **rollback SHA** = the `hyrule_cloud_version` *before* this promotion (for the rollback-by-SHA step).

## Smoke sequence

Run `scripts/smoke/vps-launch-proof.sh --base <cloud-api-base>` (it performs 1–2, 5–7
and prints the launch-proof fields); steps 3–4 and 8–9 are operator-driven.

1. **Quote** — `POST /v1/vm/quote` → `quote_id`, `payment_required`, accepted methods.
2. **Unpaid create returns 402** — `POST /v1/vm/create` without payment → **HTTP 402** (x402 gate). Proves payment is enforced.
3. **Paid create** — pay the quote via the test wallet/payment path, then `POST /v1/vm/create` → `vm_id`, `status_url`.
4. **Poll status to terminal** — `GET /v1/vm/{vm_id}/status` until `provisioned` (happy path) or `failed`. Assert the launch-proof contract fields appear: `payment_status`, `dns_aaaa_verified`, `ssh_smoke_status`, `rollback_available`, `operator_message`, `customer_message`.
5. **Verify DNS AAAA** — `dig AAAA <fqdn>` resolves to the VM's `ipv6` (or `dns_aaaa_verified: true`).
6. **Verify SSH** — `ssh_smoke_status: passed` (or a bounded SSH connect to the VM's `ipv6`).
7. **Record rollback SHA** — capture the pre-promotion `hyrule_cloud_version`.
8. **Demonstrate rollback-by-SHA** — promote the **previous** `hyrule_cloud_version` via a promotion (rollback) PR + `app-promotion-deploy`; **do not** hand-edit the pin. Confirm the app serves the prior revision.
9. **Confirm monitoring/support path** — Icinga/Discord reflect the deploy; the customer-facing failure copy points at the support/abuse contact.

## Acceptance

- Quote/create/status reaches `payment_required` then (paid) `provisioned`.
- A forced failure reaches `failed` with a **customer-safe** message (no XCP-NG IDs / raw operator errors) and `rollback_available`.
- DNS AAAA + SSH evidence captured; saved smoke output artifact attached to the tracker.
- Rollback-by-SHA demonstrated via promotion (not a manual pin edit), with the rollback SHA documented.
- Blocked-port policy and paid-VM cap still enforced.

## Failure handling

If any step regresses customer-facing behavior, roll back by promoting the previous
`hyrule_cloud_version` (step 8) and stop. Do not leave a half-provisioned paid VM.
