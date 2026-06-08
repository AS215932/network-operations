# LangGraph Change Controller

The Change Controller is the LangGraph runtime entrypoint for the Hyrule
Engineering Loop. It owns state initialization, graph routing, circuit breakers,
and final packaging for human PR sign-off.

## Responsibilities

- Classify the change.
- Load source-of-truth context.
- Select required senior role nodes.
- Run required roles in parallel when possible.
- Apply implementation tranches through coding-agent nodes in later phases.
- Run validation gates.
- Parse failures into structured `validation_errors`.
- Route back to remediation nodes.
- Stop after three retries in any domain.
- Produce rollout, rollback, and NOC handoff metadata.

## State Contract

The controller uses the Python `GraphState` in
`src/hyrule_engineering_loop/state.py` as the source of truth. The YAML shape
for human-readable handoff is:

```yaml
change_id: "<short slug>"
repos_touched: []
change_class: ""
risk_level: "low|medium|high|critical"
customer_impact: "none|possible|expected"
requires_live_telemetry: false
requires_noc_context: false
requires_deploy_window: false
requires_human_approval: false
source_of_truth_files: []
role_reviews:
  network_architect: "required|not_required|complete"
  systems_engineer: "required|not_required|complete"
  devops_netops: "required|not_required|complete"
  security_auditor: "required|not_required|complete"
  finops_integrity: "required|not_required|complete"
mcp_schema_breaking: false
emulated_lab_verified: "not_applicable|pending|passed|failed"
implementation_tranches: []
validation_gates: []
rollback_plan: ""
noc_handoff: ""
```

## Conditional Routing

- App-only changes require Systems Engineer + DevOps/NetOps.
- Cloud API, VPS provisioning, quota, payment, or metering changes also require
  FinOps.
- Network/infra changes require Network Architect + Systems +
  DevOps/NetOps.
- Routing, firewall, Vault, WireGuard, RPKI/IRR, and tenant-isolation changes
  require Security review.
- `routing_bgp_frr` and `firewall_policy` require emulated lab validation
  unless a human records explicit risk acceptance.
- Production apply is never automatic from the development loop.

## Failure Routing

- FinOps failures route to FinOps + Systems remediation.
- Security failures route to Security remediation.
- Network/routing/firewall failures route to Network remediation.
- Runtime/service failures route to Systems remediation.
- CI/CD, render, deploy sequencing, Vault rendering, smoke, drift, or rollback
  workflow failures route to DevOps/NetOps remediation.
- Any retry counter at `3` exits to human sign-off.
