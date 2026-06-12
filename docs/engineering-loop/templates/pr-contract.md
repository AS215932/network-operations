# PR contract template
#
# Rendered by the publish boundary (pr.py) from run state. Enforced, not
# suggested: a loop-generated PR without these sections does not publish.

## Intent

<!-- 1–2 sentences: what and why. From the task spec. -->

## Change class / risk

- Change class: `<change_class>`
- Risk tier: `<risk_level>`, customer impact `<customer_impact>`

## AI transparency

- Backend: `<backend>` (`<provider>/<model>`, tier `<tier>`)
- Iterations: `<n>`, remediation rounds: `<n>`, cost: `<cost>`
- Role judgments: `<role>: approve (provider/model)` per required role
- Trace: `<loop_trace.json path / artifact link>`

## Evidence

<!-- Gate-by-gate results from the authoritative re-run, diff stats,
     and for routing/firewall changes the lab (Batfish/Containerlab)
     outcome. Evidence, not promises. -->

## Human focus areas

<!-- 1–2 named areas where machine verification is weakest and human
     judgment is genuinely required (e.g. "transaction boundary in
     services/intents.py", "prefix-list ordering"). -->

## Rollout notes

<!-- Deploy path and ordering, per docs/agentic-development-loop.md. -->

## Rollback plan

<!-- Deterministic command/workflow. -->

## NOC handoff

- expected alerts:
- expected duration:
- affected hosts/services:
- rollback trigger:
- operator command/workflow:

## Post-deploy checks

<!-- Health metrics + observation window for the break-glass handshake. -->
