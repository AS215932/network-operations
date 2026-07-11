# Agentic coordination LHP-v2 shadow cutover

This is a staged migration, not a flag day. Every stage is one reviewed
`network-operations` promotion PR with a rollback SHA and Observatory evidence.

## Preconditions

1. Merge this dark `network-operations` scaffold first. Confirm the AS215932
   Promotion Bot App secrets are available to `agent-core`, SOC, NOC,
   Engineering, Knowledge, and the Agentic Observatory.
2. Merge the six green app PRs; each successful `main` CI run dispatches its
   exact SHA into the shared promotion PR.
3. Promote only those merged 40-character SHAs. This scaffold intentionally
   leaves new components at `main` and disabled, so it cannot be applied early.
4. Bootstrap the coordinator/SOC Vault scopes and add `coordinator_secret` to
   each existing loop scope. Verify all five secrets match their coordinator
   identity entry and no scope contains another identity's key.
5. Provision `soc`, enforce organization 2FA, create the GitHub OAuth app, add
   the `ops` team, and store the OAuth client plus a read-only owner policy
   token in `kv/agentic-observatory`.

## Stages

| Stage | Change | Exit evidence |
| --- | --- | --- |
| 0 dark deploy | Pin merged SHAs; let the promotion apply destination firewalls before coordinator/SOC; publish the checked-in Prometheus target to `mon`; keep all workers/timers disabled. | Coordinator/Postgres/Vault/logging/node-exporter health green; no queued work. |
| 1 shadow projection | Enable coordinator, NOC worker, Knowledge worker, SOC posture in `shadow`, and SOC handoff worker. Keep Engineering coordinator intake and Observatory handoff actions off. | Four fresh loop heartbeats; NOC/SOC case projections reconcile with legacy counts; no unintended writes. |
| 2 Observatory dual-read | Set `agentic_observatory_coordinator_enabled=true`; keep `handoff_approval,handoff_cancel` off. | Owners map to senior, `ops` to operator, other members denied; central and legacy case samples reconcile for seven days. |
| 3 controlled approvals | Add `handoff_approval,handoff_cancel`, then enable Engineering coordinator intake. | Ten operator-tier dry draft-PR handoffs and three senior-tier handoffs complete with exact scope hashes, leases, results, and source verification. |
| 4 LHP-v2 source of record | Stop legacy LHP-v1 mirroring/callback intake after a final reconciliation window. | No legacy-only handoffs for seven days; coordinator backup/restore exercised. |
| 5 SOC probe dry | Promote SOC to `probe_dry`, `max_tier=2`, global probe switch on, probe timer on. | At least ten senior-approved plans validate without network execution; bounds and owned citations visible in Observatory. |
| 6 SOC probe live | Promote to `probe_live` only after SOC insight/approval criteria pass. | Bounded probes remain within 3 targets, 32 ports, concurrency 2, 1 req/s/target, 100 requests, and 10 minutes; zero remediation attempts. |

## Rollback

- First disable the affected worker/timer; do not delete coordinator records.
- For a control-plane issue, remove `handoff_approval,handoff_cancel`, disable
  Engineering coordinator intake, and return Observatory reads to legacy NOC.
- For SOC, demote one mode rung and stop `soc-probes.timer`; setting
  `SOC_REDTEAM_ALLOW_ACTIVE_PROBES=0` is the hard execution kill switch.
- Restore app pins through a reviewed promotion PR. Do not edit a live host or
  reuse an approval after its scope hash or expiry changes.
