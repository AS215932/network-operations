# Hyrule Engineering Loop v2 — Architecture

Status: **approved design, pre-implementation**. This document is the
canonical specification that the v2 refactor (roadmap phases B–G, see
[v2-roadmap.md](./v2-roadmap.md)) is built against. The running v1
implementation is documented in
[docs/agentic-development-loop.md](../agentic-development-loop.md) and stays
authoritative for current behavior until each phase lands.

## Why v2

v1 proved the control plane: classification, the six-senior-role matrix,
gates, policy guards, worktree promotion, the human PR boundary, trace, and
the NOC handoff. What it cannot yet do is *build real things* or *run
unattended*:

- The implementation writer is a **one-shot structured completion**
  (`llm.py`). It receives truncated source context and must emit complete
  file contents (`create`/`replace`) in a single response. It cannot explore
  the repo, run a test, read an error, or iterate. That caps output quality
  at docs scaffolding.
- Role reviewers approve the **request text before any diff exists** — the
  Network Architect never sees the FRR change it is approving.
- There is no memory between runs, no intake beyond an operator prompt, no
  scheduler, and no budget enforcement.

The v2 inversion, grounded in current loop/harness-engineering practice
(Osmani's loop-engineering / self-improving-agents / harness-engineering
series, Anthropic's long-running harness design, Lance Martin's agent design
patterns):

> v1 simulated a coding agent inside LangGraph. v2 uses LangGraph to **drive
> a real coding-agent harness** inside a guarded worktree, judges the
> resulting diff, and learns from every run.

Everything that made v1 safe is retained: the mutation boundary, policy
guards, model-policy tiering, loop trace, NOC handoff, the break-glass
rollback handshake, and the hard Engineering-Loop / NOC-Loop separation.

## Decisions of record

- v2 ultimately lives in a dedicated repo (`AS215932/engineering-loop`,
  roadmap phase G). Until extraction, code keeps evolving in place in
  `network-operations` so history transfers via `git subtree split`.
- **Harness-agnostic**: loop state, prompts, skills, task specs, and gates
  are plain files plus a CLI. Pi `/loop` remains the interactive entry
  point; the same loop is drivable headless (`pi` non-interactive,
  `claude -p`, systemd timer, CI).
- **PR-only autonomy**: the loop may pick work, implement, verify, file
  issues, and open draft PRs. Every merge and every production apply stays
  human-gated. No auto-merge tier exists.

## Top-level flow

```text
INTAKE (the heartbeat)
  /loop <prompt> (Pi)            GitHub issues: loop:approved
  signal miners (Icinga/Prometheus/drift/nightly CI, read-only) ──> triage
        triage = scored candidates filed as issues labeled loop:candidate
        a human relabels loop:candidate -> loop:approved
            |
            v
PLAN  planner + role plan-consults
        -> tasks/<change-id>.md  (sprint contract: acceptance criteria,
           risk, required roles, gates, budget, allowed paths)
            |
            v
RUN (one tranche, fresh context per run)
  repo adapter: clean checkout -> branch-backed worktree
  AgentBackend.execute(spec, worktree)        <- pi | claude -p | mock
        iterative: explore, edit, run gates itself, react to failures
  authoritative gate re-run                    <- loop re-runs gates itself
  diff policy guard                            <- paths, secrets, size, scope
  role evaluators judge the DIFF               <- per role matrix; read-only
        findings + retry < 3  ──> back to AgentBackend with findings
        retry == 3 or policy fail ──> human_signoff
            |
            v
PACKAGE   PR contract + rollout/rollback notes + NOC handoff + trace
            |
            v
HUMAN GATE   operator approves persisted state (Pi /loop, CLI)
            |
            v
PUBLISH   push branch + draft PR (never merges)
            |
            v
LEARN   reflection -> journal entry + proposed lessons / skill edits
        memory/ is injected into the next run's planner and backend
```

## Components

### 1. AgentBackend — the generation core

New module `src/hyrule_engineering_loop/backend.py`.

```python
class AgentBackend(Protocol):
    def execute(
        self,
        *,
        task_spec: TaskSpec,          # parsed tasks/<change-id>.md
        worktree: Path,               # branch-backed worktree, already created
        constraints: BackendConstraints,  # budgets, tool scope, read_only
    ) -> AgentRunResult: ...

@dataclass
class AgentRunResult:
    diff: str                  # git diff captured from the worktree
    changed_paths: list[str]
    transcript_path: Path      # full harness transcript, kept out of trace
    gate_evidence: list[GateResult]   # gates the agent ran itself (advisory)
    iterations: int
    wall_clock_seconds: float
    cost: CostReport           # tokens / dollars where the harness reports them
    status: Literal["completed", "budget_exhausted", "failed"]
```

Implementations:

- `PiBackend` — non-interactive `pi` invocation in the worktree.
- `ClaudeCodeBackend` — `claude -p --output-format json` with permission
  flags pinned (no network-credentialed tools, repo-scoped filesystem).
- `MockBackend` — deterministic; absorbs the v1 writer semantics
  (`create`/`replace` whole-file mutations from `llm_mock_responses`) so the
  existing phased test suites keep passing without API keys and without a
  harness binary.

Execution rules:

- The backend runs **inside the promoted worktree** (worktree creation moves
  *before* implementation). The v1 temp-workspace + whole-file mutation path
  is retired from the live flow and survives only inside `MockBackend`.
- Injected context: the task spec, the skill index (progressive disclosure —
  full `SKILL.md` files are read on demand from the worktree), and the
  target repo's `memory/lessons/<repo>.md`.
- Environment hygiene: no production credentials, no Vault, no fleet SSH in
  the backend environment. The backend gets the repo, its toolchain, and
  nothing else. Headless runs never execute on the privileged `ci` runner.
- Budgets from the spec are hard limits: max iterations, max wall-clock, max
  tokens/cost where the harness reports them. When a harness reports no
  cost/token figures, the loop enforces the iteration and wall-clock limits
  alone and records the missing cost report in the trace — budget
  enforcement is never silently skipped. Exhaustion returns
  `budget_exhausted` and routes to remediation/human sign-off, never to a
  silent retry.
- `model-policy.yml` grows a `backends:` section so the same
  tier/risk/retry escalation that selects models also selects the executor
  (cheap harness+model combos for routine tranches, stronger for high risk).

### 2. Task specs — sprint contracts

`tasks/<change-id>.md`, template at
[templates/task-spec.md](./templates/task-spec.md). YAML frontmatter carries
the machine-readable contract (change_class, risk_level, customer_impact,
repos + allowed paths, required roles, selected gates, budgets); the body
carries intent, **testable acceptance criteria**, explicit done-conditions,
non-goals, and a rollback sketch.

A planner stage (model-routed like today's classifier) expands intake text
into the spec. `/loop --plan` keeps feeding Pi Plan-Mode output into the
same path. Evaluators later grade the diff *against the spec's criteria* —
"done" is defined before generation starts, which is the structural defense
against the agent declaring success.

### 3. Two-phase role participation — all six roles preserved

The v1 role matrix is kept verbatim (change class + risk selects required
roles; `mixed`/high-risk requires all six; the graph cannot exit without
every required approval). What changes is *when and what* a role reviews:

1. **Plan consult (before implementation).** Each required role contributes
   constraints and acceptance criteria into the task spec through its lens —
   e.g. the Network Architect injects "WireGuard endpoints stay on
   underlay; update `docs/network-flows.md`; rollback = revert + BGP session
   re-check". This preserves v1's pre-implementation review value and is how
   every role's perspective shapes the work before code exists.
2. **Post-diff judgment (after gates).** Each required role rules on the
   actual diff + gate evidence against the spec's criteria, returning a
   structured verdict:

```text
verdict: approve | request_changes
findings: [ {domain, severity, path?, message, suggested_remediation?} ]
evidence_reviewed: [ ... ]      # what the role actually looked at
```

Generation vs judgment is the load-bearing distinction. Generation needs
iteration, so it moves to the backend. Judgment over a bounded artifact
(diff + evidence + criteria) is legitimately a structured call — **except**
for high-risk roles, which run in **read-only agentic mode** (the same
`AgentBackend` with `read_only=True`) so the architect can open full
configs, grep `docs/network-flows.md`, or inspect Batfish output before
ruling, instead of judging from one prompt of truncated context.

Findings feed back to the backend as remediation context. The 3-strike
retry counter per domain and the circuit breaker to `human_signoff` are
unchanged from v1. Evaluation *depth* (model tier, agentic vs structured,
evidence required) scales with risk; role *coverage* does not shrink.

### 4. Gates — the loop re-verifies, never trusts

The backend runs gates itself while iterating (that is what makes it
effective), but its gate results are **advisory evidence only**. After the
backend completes, the loop re-runs the authoritative gate set from the
spec in the worktree: repo gates per `docs/agent-loops/acceptance-gates.md`
(pytest/ruff/mypy, `npm run check`, Ansible validate renders), and for
`routing_bgp_frr` / `firewall_policy` the existing tiered NetOps labs
(`scripts/ci/iac-static.sh`, Batfish, Containerlab) on their normal trusted
triggers. Gate failures parse into `validation_errors` exactly as today.

### 5. Diff policy guard

`policy.py` shifts from pre-validating proposed mutations to validating the
**resulting `git diff`**: denied path globs, denied content patterns,
max-changed-files, max-file-bytes — plus a new check that every changed path
falls under the spec's allowed prefixes ("touch only what you're asked to
touch"). Protected-branch, allowed-remote, and handoff-dir guards are
unchanged. The changed-file cap doubles as the comprehension-debt control:
tranches stay small enough for a human to genuinely review.

### 6. Memory — the self-improvement flywheel

New `memory/` tree:

```text
memory/
  lessons/<repo>.md       # accumulated rules, AGENTS.md-style, human-curated
  proposals/<change-id>.md  # loop-proposed lesson/skill edits, await human merge
  journal/<change-id>.md  # per-run lab notes: attempts, failures, findings, cost
```

- A new `reflection` node runs after publish or human sign-off: it distills
  the run (what failed, what the evaluators caught, what surprised the
  backend) into a journal entry and, when warranted, a **proposed** lesson.
- Humans merge proposals into `memory/lessons/` — human curation is
  deliberate; the loop never edits its own rulebook directly.
- Every lesson entry must trace to a specific failure (the ratchet). When a
  failure class recurs, it graduates from a lesson into a deterministic gate
  or policy rule, and the lesson is retired.
- Lessons and the journal tail are injected into the planner and backend on
  the next run touching that repo.

### 7. Skills — workflows, not role essays

`skills/<name>/SKILL.md` (see the `skills/` tree at the repo root):
frontmatter with trigger conditions, workflow steps with **checkpoints that
demand evidence**, an **anti-rationalization table**, and exit criteria.

Initial set: the six senior-role skills, the implementation-tranche skill
(the backend's working contract), and ISP-procedure skills that previously
lived only as CLAUDE.md prose (firewall three-step, monitoring
onboarding three-step). The loop injects skills itself (index up front, full
text on demand), which keeps the system harness-agnostic instead of
depending on any one CLI's native skill mechanism. v1's
`docs/agent-loops/*.md` files remain until phase C rebinds prompt loading.

### 8. Intake and triage — the heartbeat

New `src/hyrule_engineering_loop/intake/`:

- `github_issues.py` — scans org repos for actionable work. Queue
  convention is labels: `loop:candidate` (machine-proposed, awaiting human
  triage), `loop:approved` (eligible for autonomous runs). Issue bodies
  follow the org's existing self-contained Context / Action items / Related
  convention.
- `signals.py` — read-only miners over Icinga and Prometheus (via
  hyrule-mcp), nightly `drift-detection` artifacts, and `netops-nightly`
  failures. Miners emit *candidate issues*, never direct runs, and dedupe
  against open issues before filing. NetFlow joins later as another miner.

The triage inbox is therefore the GitHub issue tracker itself — reviewable
from anywhere, durable, and already monitored by humans.

### 9. Operations lane — long-running mode

`hyrule-engineering-loop daemon --once`: pick exactly one `loop:approved`
item, run the full flow, end with a draft PR or a journaled failure, exit.
A systemd timer (operator machine first; later a dedicated `loop` VM
onboarded through the standard flows/firewall/monitoring three-steps) or a
manually dispatched workflow provides the schedule.

Safety rails:

- a run lock (one loop run at a time);
- per-run and per-day budgets (tokens, cost, wall-clock, iterations);
- kill criteria: an unchanged diff across 3 consecutive remediation rounds
  (matching the role/gate retry circuit breaker) aborts the run;
- a Discord webhook summary per run and an Icinga passive check
  ("loop ran / loop stuck / loop over budget");
- never on the privileged `ci` runner — the backend executes generated code.

### 10. PR contract

`pr.py` renders a structured PR body from the run state
([templates/pr-contract.md](./templates/pr-contract.md)): intent in two
sentences, evidence (gate outputs, diff stats), risk tier + **AI
transparency** (backend, models, iterations from the trace), one or two
named human focus areas, and the rollout / rollback / NOC handoff sections
that v1 documented as a suggestion. In v2 the template is enforced at the
publish boundary, not suggested.

## Boundaries that do not change

- **NOC separation**: the Engineering Loop designs and builds; the NOC Loop
  observes and remediates. The forbidden/allowed connection lists in
  [docs/agentic-development-loop.md](../agentic-development-loop.md) carry
  over verbatim, including the break-glass rollback handshake.
- **Human gate**: `approval_decision: approved` on a persisted state
  artifact remains the only path to push/PR. Draft PRs only. No auto-merge.
- **Policy guards** stay fail-closed: any policy failure routes to
  `human_signoff`, never around it.

## State and artifact layout (target)

```text
.engineering-loop-state/<change-id>.json   # persisted GraphState (as today)
tasks/<change-id>.md                       # sprint contract
memory/{lessons,proposals,journal}/        # flywheel
skills/<name>/SKILL.md                     # injected workflows
handoff/{noc_handoff.json,loop_trace.json} # unchanged shapes, plus backend
                                           # transcript path + cost report
worktrees/<repo>-<change-id>/              # backend works here
```

`loop_trace.json` keeps its sanitized-summary discipline; backend
transcripts are stored beside it but never inlined.
