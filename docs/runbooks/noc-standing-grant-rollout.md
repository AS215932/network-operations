# NOC autonomy rollout — insight records, no-op guards, standing grants

Staged relaxation of NOC human gates. Every step is an env promotion PR
against `ansible/roles/vault_agent/templates/noc-agent.env.ctmpl.j2` (plus the
hyrule-mcp env where noted), applied through the standard gated flow with
Icinga snapshots. Each step has a burn-in and a measured promotion criterion
before the next.

## Step 0 — insight records on (read-only reporting)

Adds to `noc-agent.env.ctmpl.j2`:

```
NOC_INSIGHT_RECORDS_ENABLED=1
NOC_INSIGHT_KNOWLEDGE_CITATIONS=1
```

Prereqs: agent-core collector pin includes `/v1/insights`
(`agent_core_collector` role), noc-agent deployed at a SHA with the proactive
insight emitter, `HYRULE_NOC_AGENT_CORE_TRACE=1` + collector URL already set
(they are). Optional: `NOC_OBSERVATORY_PUBLIC_URL=https://observatory.servify.network`
for digest deep links.

Verify: `GET <collector>/v1/insights?loop=noc` returns decisions including
`stay_silent` records; volume stays bounded (reassert default 6h, cap
32/cycle).

## Step 1 — no-op rollback guards (zero-risk validation of the execute path)

Adds (noc-agent env + hyrule-mcp env):

```
NOC_ENABLE_NOOP_ROLLBACK_GUARDS=1
HYRULE_MCP_ENABLE_NOOP_GUARDS=1
```

`NOC_ENABLE_APPROVED_EXECUTION=1` is already live. With guards on, approved
proposals route through the inert `prepare_commit_confirm` guard (approval
state `noop_guards_prepared`, zero mutation), exercising the full
approve → execute → trace → insight path.

Burn-in: ≥ 2 weeks. Watch `/insights?loop=noc` and case traces; every
approved proposal should show a prepared guard and no execution failures.

## Step 2 — first Tier-0 standing grant (acknowledge_icinga)

Adds:

```
NOC_STANDING_GRANT_ACTION_CLASSES=acknowledge_icinga
NOC_STANDING_GRANT_ACK_TTL_S=86400
```

Behavior (enforced in `app/graph/nodes.py`, covered by
`tests/test_standing_grant.py` in the noc-agent repo):

- Only proposals whose **every** structured action is `acknowledge_icinga`
  skip the per-incident approval; mixed/unsupported sets still wait for a
  human.
- Execution enforces the envelope: exactly one matching **WARNING**-state
  Icinga problem (never CRITICAL, never ambiguous), TTL'd ack, `notify=false`.
- Fully audited: synthetic operator `standing-grant` in the decision trace, a
  Discord line, and the insight record.

**Promotion criterion**: step-1 burn-in complete **and** NOC IDQ ≥ 0.70 over
≥ 25 labeled insights (`hyrule-knowledge insights metrics --loop noc`), with
the labels accumulated via the Observatory insight inbox.

Rollback for any step: revert the env promotion PR, re-apply, verify via
Icinga snapshot diff. Kill switches: `NOC_STANDING_GRANT_ACTION_CLASSES=`
(empty) disables grants instantly; `NOC_INSIGHT_RECORDS_ENABLED=0` stops the
stream.
