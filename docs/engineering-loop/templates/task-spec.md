# Task spec template — tasks/<change-id>.md
#
# The sprint contract for one engineering-loop tranche. Frontmatter is the
# machine-readable contract; the body is the human/agent-readable intent.
# "Done" is defined here, before generation starts — evaluators grade the
# diff against the acceptance criteria below, not against vibes.

---
change_id: EXAMPLE_CHANGE
change_class: app_bugfix          # one of the v1 change classes
risk_level: low                   # low | medium | high | critical
customer_impact: none             # none | possible | expected
repos:
  hyrule-cloud:
    allowed_paths: ["hyrule_cloud/", "tests/"]
required_roles: []                # filled by the planner from the role matrix
gates: []                         # filled from acceptance-gates.md per repo/class
budget:
  max_iterations: 20
  max_wall_clock_minutes: 45
  max_cost_usd: 5.00
intake_source: "operator"         # operator | issue:<repo>#<n> | signal:<miner>
---

## Intent

One or two sentences: what this tranche changes and why.

## Acceptance criteria

Testable statements only. Each criterion must be verifiable by a gate, a
test, or direct inspection of the diff.

1. ...
2. ...

## Done-conditions

The run is complete when all acceptance criteria hold AND:

- all gates in the frontmatter pass in the worktree;
- the diff touches only `allowed_paths`;
- every required role judgment is `approve`.

## Non-goals

What this tranche explicitly does not do (scope fence for the backend).

## Role consult notes

Appended by the plan-consult pass — one subsection per required role with
the constraints that role demands of the diff.

## Rollback sketch

How this change is undone if it regresses after deploy (feeds the PR
contract's rollback section and the NOC handoff).
