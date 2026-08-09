# Learning Lift (LL) replay harness

Reference: arXiv 2605.06717 §4.3 (Equation 4).

## What Learning Lift measures

Learning Lift quantifies whether operator feedback on earlier recurring
scenarios actually improves later decisions.  We compute it as:

```
LL = IDQ(adapted) − IDQ(frozen)
```

- **IDQ(adapted)** — the Insight Decision Quality of a policy that has received
the labels from the *earlier* round of the same scenario family.
- **IDQ(frozen)** — the IDQ of the exact same policy version, run against the
*later* decision points without any exposure to those earlier labels.

Positive LL on a recurring scenario family is the strongest evidence that the
learning loop (feedback → policy update → later replay) produces better timing
and action choice.  It justifies gate relaxation; zero or negative LL is a
signal to pause promotion and investigate the feedback path.

## Scenario format

A replay scenario file is a small YAML fixture.  It pins a **fingerprint family**
and a sequence of decision points with ground-truth labels.

```yaml
replay_scenario:
  family_id: "bgp-flap-hotspot-northbound-weekends"
  description: >
    BGP session flap on cr1.de1 weekends. Same vendor / same RPKI-sourced prefix.
    Fingerprint is stable across weeks; only the exact timestamp and prefix vary.
  source_ledger_pattern: "ledger/insights/noc/bgp-flap-*"
  min_span_days: 14

  decision_points:
    # Decision point A – the "past round"
    - id: dp-a
      timestamp: "2025-06-14T08:32:00Z"
      fingerprint:
        loop: noc
        posture: bgp
        signal_hash: "a1b2c3d4"
      labels:
        - operator: "svag"
          verdict: "surfaced"
          action: "prepare_commit_confirm"
          comment: "real flap, standard path"

    # Decision point B – the "later round", same family
    - id: dp-b
      timestamp: "2025-06-21T09:05:00Z"
      fingerprint:
        loop: noc
        posture: bgp
        signal_hash: "a1b2c3d4"
      labels:
        - operator: "svag"
          verdict: "surfaced"
          action: "prepare_commit_confirm"
          comment: "same signature, correct again"
```

- `family_id` is a human-readable stable identifier.
- `source_ledger_pattern` tells the knowledge-repo ingest where to look for the
  matching `InsightDecisionRecord` stream.
- `decision_points` are ordered.  Labels from `dp-a` are fed only into the
  **adapted** policy; the **frozen** policy is the codebase version that was
  current at `dp-a.timestamp` and is never re-trained.
- Both policies are then evaluated on `dp-b` using the same evaluation harness
  that the 53-case deterministic fixture suite uses.

## Deterministic replay contract

The replay harness is deterministic and fixture-first, just like the existing
53-case insight-policy regression suite:

1. **Freeze**: check out the policy version pinned by the first decision point
   (`dp-a`).  Call this `policy_frozen`.
2. **Adapt**: start from `policy_frozen`, apply the `dp-a` labels as operator
   feedback, and run the policy-update step once.  Call this `policy_adapted`.
3. **Evaluate both** on `dp-b` using the identical feature vector that was
   extracted at `dp-b.timestamp`.  Compute `IDQ_frozen` and `IDQ_adapted`.
4. **Report**: `LL = IDQ_adapted - IDQ_frozen`.

The harness runs in the `knowledge` repo (`evals/replay/`).  It needs no live
infrastructure, no credentials, and no non-deterministic LLM calls — the feature
vectors and labels are the fixture.

## Usage in promotion runbooks

### SOC mode promotion

After the required `idq` and `cgs` thresholds, an additional positive evidence
line is expected before `handoff_live`:

- `LL ≥ 0` on at least one recurring SOC scenario family (≥ 5 labels in the
  family, spanning ≥ 7 days).  If `LL` is negative, promotion to `handoff_live`
  is blocked pending investigation of the feedback/update pipeline.

### NOC standing-grant rollout

Before any `NOC_STANDING_GRANT_ACTION_CLASSES` expansion beyond the initial
`acknowledge_icinga` grant, the runbook should show:

- `LL ≥ 0` on at least one recurring NOC scenario family with the grant-relevant
  action class present in the labels (≥ 5 labels, spanning ≥ 7 days).

## Metrics output

The knowledge-repo CLI reports LL alongside IDQ and CGS:

```bash
cd ~/Dev/knowledge
uv run hyrule-knowledge insights metrics --loop soc
```

Sample output:

```
loop: soc
  idq:          0.82
  cgs:          0.71
  learning_lift: 0.09  (family: bgp-flap-hotspot-northbound-weekends, n=2)
  label_count:  47
```

When no replay scenario has been evaluated yet, `learning_lift` is reported as
`null` with a note to run the replay harness.  The placeholder is removed once
at least one family is populated.

## Rollout and maintenance

- Scenario families live in `knowledge/evals/replay/scenarios/<loop>/`.
- Each family is a YAML file.  New families are added by curators; the same
  branch-protection rule that guards `ledger/insights/` also guards
  `evals/replay/scenarios/`.
- The nightly knowledge-refresh job (`auto-merge.yml`) skips scenario files —
  they are human-curated fixtures, not machine-generated projections.
- Operators may propose new families via the standard knowledge PR flow.  The PR
  must include the fixture, the computed `LL` from a local replay run, and a
  justification for why the fingerprint is stable enough to be considered
  recurring.
