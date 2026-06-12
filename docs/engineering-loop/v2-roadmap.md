# Hyrule Engineering Loop v2 — Roadmap

Phases B–G of the v2 refactor (phase A was the design itself — the
[architecture spec](./v2-architecture.md), templates, and the initial
`skills/` tree). Each phase is one reviewable PR with green gates and is
dogfooded as a task spec when the loop is far enough along to build itself.

Tracked as GitHub issues in `AS215932/network-operations`; this file is the
ordered overview.

## B — AgentBackend + worktree-first execution

The generation core swap.

Scope:

- `src/hyrule_engineering_loop/backend.py`: `AgentBackend` protocol,
  `AgentRunResult`, `BackendConstraints`, `CostReport`.
- `MockBackend` absorbing v1 writer semantics (whole-file `create`/`replace`
  from `llm_mock_responses`) so existing tests pass without keys/binaries.
- `PiBackend` and `ClaudeCodeBackend` (non-interactive subprocess drivers,
  environment-hygiene enforced: no Vault, no fleet SSH, repo-scoped).
- Graph reorder: repo adapter + worktree creation move **before**
  implementation; `delegate_implementation` node replaces
  `implementation` + `workspace_writer`; the temp-workspace path is removed
  from the live flow.
- `policy.py` validates the resulting `git diff` (denied globs/content,
  size caps, spec-allowed path prefixes) instead of proposed mutations.
- `model-policy.yml` `backends:` section + selection logic in
  `model_policy.py`.
- `backend-canary` CLI command (docs-only live canary, successor of
  `writer-canary`).

Acceptance criteria:

1. `uv run --group dev python -m pytest -q` green with no API keys and no
   harness binaries installed (MockBackend everywhere).
2. `backend-canary --dry-live` assembles spec/skills/lessons context and the
   backend command line without executing a harness.
3. A live `backend-canary` against a sibling repo produces a docs-only diff
   in a branch-backed worktree, passes the diff policy guard, and stops
   before approval/push.
4. Policy guard rejects: a diff touching paths outside the spec allowlist, a
   diff introducing a secret-pattern match, and a diff above the file cap —
   each with structured `validation_errors`.
5. Budget exhaustion in the backend returns `budget_exhausted` and routes to
   human sign-off, observable in the trace.

## C — Task specs + two-phase roles

"Done" defined before generation; roles judge the artifact.

Scope:

- `tasks/<change-id>.md` parser (frontmatter + body sections per the
  template); planner stage expands intake text into a spec; `/loop --plan`
  feeds Plan-Mode output into the same path.
- Role plan-consult pass writes role constraints/criteria into the spec.
- Role nodes become post-diff judges: structured verdict schema
  (`approve | request_changes` + findings), gate evidence attached.
- Read-only agentic evaluation mode (backend with `read_only=True`) for
  high/critical risk and for `routing_bgp_frr` / `firewall_policy` roles.
- Remediation router feeds findings back to the backend; 3-strike circuit
  breaker preserved.
- Prompt loading rebinds from `docs/agent-loops/*.md` to `skills/*/SKILL.md`.

Acceptance criteria:

1. A run without a task spec is refused (except `run`/`dry-run` test paths).
2. The spec records each required role's consult output; the trace shows
   both consult and judgment events per role.
3. A mocked failing judgment routes findings back to the backend and the
   diff demonstrably changes on the retry (test fixture).
4. Role matrix coverage is byte-identical to v1 for every change class
   (regression-tested against `required_roles_for_state`).
5. High-risk evaluators run read-only: a write attempt from evaluation mode
   fails the run (test fixture).

## D — Memory + reflection flywheel

Scope:

- `memory/{lessons,proposals,journal}/` tree + loaders.
- `reflection` node after publish/sign-off: journal entry always; proposed
  lesson when a failure pattern is detected.
- Lessons + journal tail injected into planner and backend context for the
  target repo.
- `loop lessons` CLI + Pi `/loop lessons` to review/merge proposals
  (merge itself is a human git action).

Acceptance criteria:

1. Every completed or signed-off run writes exactly one journal entry.
2. A run that fails the same gate twice produces a lesson proposal that
   names the gate and the failing pattern.
3. Proposals never modify `memory/lessons/` directly (test: proposals dir
   only).
4. A lesson present for the target repo appears verbatim in the backend's
   injected context (dry-live assembly test).

## E — Intake + triage

Scope:

- `intake/github_issues.py`: org-repo scan, `loop:candidate` /
  `loop:approved` label protocol, self-contained issue bodies, dedupe
  against open issues.
- `intake/signals.py`: read-only miners — Icinga unhandled-alert summary,
  Prometheus rule breaches, nightly drift artifacts, `netops-nightly`
  failures — each emitting candidate issues only.
- Scoring/dedupe so the inbox stays low-noise.
- Pi `/loop triage` to review the candidate queue.

Acceptance criteria:

1. Miners are read-only: no mutating MCP/gh calls outside issue creation.
2. A signal already represented by an open issue files nothing.
3. Candidate issues carry Context / Action items / Related sections and the
   `loop:candidate` label; nothing self-promotes to `loop:approved`.
4. `daemon --once` (phase F dependency) only consumes `loop:approved`.

## F — Operations lane

Scope:

- `daemon --once`: pick one `loop:approved` item, full run, draft PR or
  journaled failure, exit. Run lock; per-run and per-day budgets; kill
  criteria (no diff progress across N remediation rounds).
- Discord webhook run summary; Icinga passive check (`loop ran / stuck /
  over budget`).
- systemd service + timer units (operator machine first; a dedicated `loop`
  VM later goes through the standard network-flows + firewall + monitoring
  onboarding).
- Never schedule on the privileged `ci` runner.

Acceptance criteria:

1. Two concurrent `daemon --once` invocations: the second exits immediately
   on the lock.
2. Budget exhaustion mid-run produces a draft-PR-less journaled failure and
   a Discord summary, and the next run is unaffected.
3. The Icinga passive check goes stale-alert when the timer stops firing.
4. End-to-end: a seeded `loop:approved` docs issue becomes a draft PR with
   the PR contract body, with no human input between timer fire and PR.

## G — Extraction to AS215932/engineering-loop

Scope:

- `git subtree split` of: `src/hyrule_engineering_loop/`, loop test suites,
  `docs/agent-loops/`, `docs/agentic-development-loop.md`,
  `docs/engineering-loop/`, `skills/`, `integrations/pi/`,
  `model-policy.yml`, `engineering-loop-policy.yml`, `pyproject.toml`/lock.
- New repo CI: `pytest` + `ruff` + `mypy --strict` on the **unprivileged
  `ci-pr` runner** (label `hyrule-public-pr`), branch protection with those
  checks required.
- `network-operations` keeps a pointer doc; Pi extension install docs
  repoint; promotion/PR tooling configs updated for the new repo name.
- Update the org CI/CD inventory (`docs/ci/org-cicd-inventory.md`).

Acceptance criteria:

1. History for migrated files is preserved in the new repo.
2. New repo CI green on the unprivileged runner; repo added to the
   `public-pr` runner group, not `hyrule-ci`.
3. `network-operations` contains no loop runtime code afterwards; its CI
   stays green (lint/render/iac-gate unaffected).
4. A sibling-canary run from the new repo against `hyrule-cloud` works
   end-to-end.

## Sequencing notes

- B is the prerequisite for everything; C and D can land in either order
  after B (D's reflection is more valuable once C's specs exist, so C
  first is preferred).
- E only needs the CLI surface, but its issues are only consumable once F
  exists; landing E before F gives humans time to tune the miners' noise
  level on `loop:candidate` quality alone.
- G goes last deliberately: extraction is cheap once the shape is stable,
  and expensive churn if done mid-refactor.
