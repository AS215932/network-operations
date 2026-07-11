# SOC_MODE promotion — measured criteria per rung

The SOC Agent climbs `SOC_MODE` (`shadow → case_only → handoff_dry →
handoff_live → probe_dry → probe_live`) one reviewed promotion PR at a time. Each rung change edits
`soc_mode` in the target host's `host_vars` (rendered into
`/opt/soc-agent/.env` by Vault Agent) and must link the metrics evidence below
in the PR body. Rung changes are env promotion PRs — never live edits.

## Where the numbers come from

The SOC posture loop emits an `InsightDecisionRecord` per finding decision
(including deliberate silence). Records flow: SOC → agent-core collector →
`hyrule-knowledge insights sync` → committed `ledger/insights/` (reviewed via
the nightly knowledge PR). Operators label decisions in the Agentic
Observatory (`/insights?loop=soc`), which produces `InsightLabel`s on the same
path. Compute the criteria on the knowledge repo checkout:

```bash
cd ~/Dev/knowledge
uv run hyrule-knowledge insights metrics --loop soc
```

Read `idq`, `label_count`, `cgs`, and the per-loop breakdown. IDQ counts
deliberate silence; CGS covers surfaced, labeled decisions with gold evidence.

## Criteria

| Promotion | Minimum evidence |
|-----------|------------------|
| shadow → case_only | ≥ 25 labeled SOC insights spanning ≥ 14 days; IDQ ≥ 0.70; zero `unsupported` faithfulness verdicts among surfaced insights |
| case_only → handoff_dry | ≥ 40 labeled insights; IDQ ≥ 0.75; accept rate on surfaced insights ≥ 0.60 |
| handoff_dry → handoff_live | Everything above, plus ≥ 10 dry-built handoffs labeled well-formed with accept ≥ 0.80 |
| handoff_live → probe_dry | ≥ 10 successful exact-scope senior approvals; zero stale-scope accepts; all proposed targets carry A0/A1 ownership citations |
| probe_dry → probe_live | ≥ 10 dry probe plans within every contract bound; senior approval precision 1.0; zero unsupported targets; explicit owner sign-off |

Regression rule: if IDQ over the trailing 25 labels drops below the rung's
threshold, demote one rung in a fast-follow PR and note why.

## Promotion PR checklist

1. `insights metrics --loop soc` output pasted (or linked) in the PR body.
2. `soc_mode` bumped exactly one rung in host_vars; `soc_lhp_enabled` and
   friends adjusted per `docs/soc-agent/rollout.md` in the soc-agent repo.
3. Re-render + validate: `ansible-playbook playbooks/soc.yml --tags validate
   --connection=local --limit soc`.
4. Apply per the standard gated flow: Actions → `apply` workflow →
   `playbook=soc` (the workflow resolves the `soc_agent_apply=true` gate), or
   from the workstation `--tags apply -e soc_agent_apply=true`. Confirm live
   Icinga / the hyrule MCP is clean before and after either way.
5. Hard rails: below `probe_dry`, `SOC_REDTEAM_ALLOW_ACTIVE_PROBES=0` and
   `SOC_REDTEAM_MAX_TIER<=1`. Probe rungs require the central exact-scope senior
   approval, owned A0/A1 citations, and the bounded RT-2 contract. SOC never
   gets remediation credentials or sets `HYRULE_MCP_ENABLE_ACTIONS`.
