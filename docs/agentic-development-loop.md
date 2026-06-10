# Hyrule Engineering Loop

The Hyrule Engineering Loop is the autonomous development loop for the
`hyrule-*` repositories. It designs changes, coordinates coding agents,
validates implementation tranches, prepares rollout and rollback notes, and
stops for final human PR sign-off.

The Hyrule NOC Loop is separate production runtime infrastructure. It responds
to alerts, investigates production, detects drift, recommends approved
remediation, and verifies runtime recovery. Do not add development
orchestration to `hyrule-noc-agent`.

## Architecture Decision

Use two separate loops:

```text
Hyrule Engineering Loop
  - designs changes
  - coordinates coding agents
  - validates PRs
  - prepares rollout plans
  - invokes existing CI/deploy gates

Hyrule NOC Loop
  - responds to alerts
  - investigates production
  - detects drift
  - proposes/remediates approved incidents
  - verifies runtime recovery
```

Allowed connection:

```text
Engineering Loop -> deploy notes / expected impact / rollback plan -> NOC context
NOC Agent -> production alerts / drift / post-deploy symptoms -> operator feedback
```

Forbidden connection:

```text
NOC Agent -> coding agents -> PR generation
NOC Agent -> normal feature planning
NOC Agent -> CI unit-test triage unless production-impacting
```

## Internal Loop Structure

At a high level, Pi is the operator control surface, `hyrule-infra` owns the
LangGraph runtime, and sibling `hyrule-*` repos are mutation targets. The loop
does not directly commit or push during normal feature intake.

```text
Operator / Pi
    |
    | /loop <prompt>
    | /loop --plan
    v
Pi hyrule-loop extension
    |
    | writes request markdown under /tmp
    | runs: uv run hyrule-engineering-loop feature ...
    v
hyrule-infra LangGraph runtime
    |
    | reads request + source context
    | runs role nodes + gates + policy
    | writes temp workspace and promoted worktree
    v
Artifacts
    |
    +-- state/<change_id>.json
    +-- handoff/noc_handoff.json
    +-- handoff/loop_trace.json
    +-- worktrees/<repo>-<change_id>/
```

The current graph topology is:

```text
START
  |
  v
[classification]
  |
  +------------------+------------------+------------------+
  |                  |                  |                  |
  v                  v                  v                  v
[systems]       [devops/netops]   [network]          [security]
  |                  |                  |                  |
  +------------------+------------------+------------------+
                     |
                     v
             [implementation]
                     |
                     v
             [workspace_writer]
                     |
                     v
             [gate_execution]
                     |
                     v
             [workspace_cleanup]
                     |
                     v
             <remediation router>
              |        |          |
              |        |          +--> retry role nodes when gates fail
              |        +-------------> [human_signoff] when circuit breaker trips
              v
          [repo_adapter]
              |
              v
            [policy]
              |
              v
          [promotion]
              |
              v
          [package_pr]
              |
              v
             END
```

For app work, only Systems and DevOps/NetOps roles are required by default. For
network, firewall, Vault, cloud billing, or mixed changes, the classifier fans
out to the additional senior roles defined in the role matrix.

The main state and artifact flow is:

```text
feature request text
  -> GraphState.feature_request
  -> role prompts + source context
  -> structured role outputs
  -> GraphState.proposed_mutations
  -> temporary workspace files
  -> gate results / validation_errors
  -> policy decision
  -> promoted git worktree diff
  -> final state + NOC handoff + loop trace
```

The mutation boundary is intentionally split into phases:

```text
Feature intake / dry-run
  - may create temporary workspaces
  - may create local git worktrees and branches
  - must stop with approval_decision: pending
  - must not commit, push, or open PRs

Human inspection
  - operator reads promoted worktree, state, handoff, and trace
  - operator either cleans up or approves state

PR publication
  - separate command
  - requires approval_decision: approved
  - requires policy_status: passed
  - requires promotion_results
  - commits and pushes generated branch
  - creates GitHub draft PR only when explicitly requested
```

The trace answers "what did the agents do?" without exposing all context:

```text
loop_trace.json
  |
  +-- change metadata
  +-- event_count
  +-- events[]
        |
        +-- node
        +-- timestamp
        +-- input_keys
        +-- state_before
        |     +-- approval_true
        |     +-- mutation_paths
        |     +-- retry_counters
        |     +-- validation_error_count
        +-- output
              +-- approvals, statuses, mutation paths, file lists
              +-- summarized gate/promotion results
              +-- no full prompts, source contents, full diffs, or secrets
```

Pi uses one command as the daily entry point:

```text
/loop <prompt>
  -> start feature intake against autodetected hyrule-* repo

/loop --repo hyrule-web <prompt>
  -> override repo autodetection

/loop --plan
  -> read latest Plan Mode proposed plan and send it as the request

/loop
  -> interactive menu:
       start new request
       show latest summary
       show latest trace
       cleanup latest worktree
       approve latest state
```

## LangGraph Runtime

Phase 1 implements a first runnable LangGraph controller skeleton in
`src/hyrule_engineering_loop/`. It is deterministic and local-only: no LLM API
calls, no live telemetry, no production access, and no network calls.

Phase 2 staging adds the control-plane surfaces needed before live agents are
hydrated:

- an operator CLI, `hyrule-engineering-loop`;
- persisted JSON state artifacts under `.engineering-loop-state/`;
- optional `interrupt_before=["human_signoff"]` graph compilation;
- explicit local `gate_commands` execution with stdout/stderr capture;
- Markdown prompt loading from `docs/agent-loops/` for future model binding.

The Phase 2 code still does not call live LLM APIs or production telemetry.
Role nodes remain deterministic until model adapters are added behind a
structured state-patch interface.

Phase 3 adds LLM hydration and workspace mutation staging:

- role nodes invoke the structured LLM layer in `llm.py`;
- mock structured responses can be supplied through `llm_mock_responses`;
- role prompts are loaded from `docs/agent-loops/`;
- source-of-truth file contents are read and passed into role review calls;
- proposed file mutations are validated as structured outputs;
- mutations are written into an isolated temporary workspace before gates run;
- gates execute with that temporary workspace as their working directory;
- cleanup removes the temporary workspace before routing continues.

The default LLM client remains deterministic. Live provider adapters must
return the same structured schema and must not emit free-form file edits.

Phase 4 adds live-token hardening and NOC plane integration:

- `HYRULE_MOCK_LLM` defaults on; set `HYRULE_MOCK_LLM=0` to enable live HTTP
  inference.
- Live inference reads `HYRULE_LLM_API_KEY` or `OPENAI_API_KEY`.
- Live inference reads `HYRULE_LLM_BASE_URL` or `OPENAI_BASE_URL` for base URL
  overrides.
- Live inference reads `HYRULE_LLM_MODEL`, `HYRULE_LLM_TIMEOUT_SECONDS`, and
  `HYRULE_LLM_MAX_RETRIES`.
- Provider/API failures are converted to structured `validation_errors` with
  domain `llm`.
- `HYRULE_WORKSPACE_ROOT` controls where temporary mutation workspaces are
  created.
- `HYRULE_HANDOFF_DIR` or `handoff_output_dir` designates where
  `noc_handoff.json` is rendered for monitoring/NOC consumption.

Phase 5 adds branch-backed mutation promotion:

- promotion is opt-in with `promotion_enabled`;
- structured mutation keys use `repo:path` format;
- each repo must be listed in `promotion_repositories`;
- each path must match `promotion_allowed_paths`;
- git worktrees are created under `promotion_worktree_root`;
- promoted worktrees use a per-change branch name;
- git diffs are captured into `promotion_results`;
- failed promotion attempts roll back created worktrees/branches;
- successful promotion sets `requires_human_signoff` before any push or PR
  creation exists.

Phase 6 adds the PR creation boundary:

- PR publication is a separate CLI command, not a graph edge.
- The command reads a persisted state artifact.
- It refuses unless `approval_decision == "approved"`.
- It refuses unless `promotion_results` exist.
- It commits each promoted worktree.
- It pushes each generated branch to the configured remote.
- GitHub draft PR creation remains disabled unless explicitly requested.
- PR metadata is written back into the state artifact.

Phase 7 adds policy guards:

- `engineering-loop-policy.yml` defines global and repo-specific bounds.
- The graph runs a policy node after workspace cleanup and before promotion.
- Policy failures stop at human sign-off and do not reach promotion.
- Mutation paths are checked for traversal, denied globs, size limits, and
  secret-looking content.
- Gate commands are checked against an allowlist.
- Promotion branch prefixes are checked against protected namespaces.
- Promotion repo roots can be allowlisted per repo.
- PR remotes are checked before commit/push publication.

Phase 8 adds real repo adapter dry-runs:

- sibling `hyrule-*` repos can be discovered from a workspace root;
- target repos must be clean git checkouts;
- detached HEAD target repos are refused;
- base refs are verified before promotion;
- repo names are normalized into promotion paths before policy/promotion;
- `dry-run` runs graph, policy, promotion, and handoff, then stops before
  approval or PR publication.

Phase 10 adds an offline operator dry-run harness:

- a disposable local git repo and bare remote are created under an operator
  supplied root;
- the graph runs with promotion and NOC handoff enabled;
- the persisted state is approved inside the harness;
- PR publication commits and pushes to the disposable remote;
- GitHub draft PR creation uses `HYRULE_MOCK_GITHUB_PR_URL` and makes no
  network call;
- the harness verifies the same approval, push, PR body, label/reviewer, and
  handoff surfaces that operators use in normal publication.

Phase 11 adds a sibling-repo canary dry run:

- a real sibling `hyrule-*` checkout is selected from an operator supplied
  workspace root;
- the repo adapter requires a clean, attached git checkout and valid base ref;
- the canary mutation is restricted to `docs/engineering-loop-canary.md`;
- graph, repo adapter, policy, promotion, and handoff stages run;
- the harness stops before approval, commit, push, or PR creation;
- generated canary worktrees and branches are removed by default after
  verification.

Phase 13 adds feature-intake UX:

- operators provide a feature request Markdown file instead of raw graph state;
- the command targets one sibling repo and one or more allowed path prefixes;
- request text is stored in `GraphState["feature_request"]` for role review;
- source files can be passed as repo-relative paths and are loaded as
  `repo:path` context;
- offline mock mode scaffolds a docs planning artifact or accepts explicit
  `--mock-mutation` values;
- live LLM mode can propose structured mutations through the same role-node
  schema by setting `HYRULE_MOCK_LLM=0`;
- the command still stops before approval, commit, push, or PR creation.

Phase 14 adds loop trace and Pi `/loop` UX hardening:

- every graph node appends a compact trace event to `trace_events`;
- `loop_trace.json` is written beside `noc_handoff.json` when a handoff
  directory is configured;
- trace events include node name, timestamp, input keys, sanitized output
  summaries, approval state, retry counters, and mutation paths;
- trace events do not dump full prompts, source file contents, full diffs, or
  secret-bearing payloads;
- `/loop` in Pi autodetects the current `hyrule-*` repo from the working
  directory when possible;
- `/loop` with no arguments opens one menu for new requests, latest summary,
  trace location, cleanup, and approval;
- `/loop --plan` reads the current stored Plan Mode proposed plan and passes it
  to the engineering loop as the feature request.

Loop stages:

1. Intake.
2. Classify change type.
3. Load repo/source-of-truth context.
4. Populate required senior role approvals.
5. Run role review nodes.
6. Build implementation plan.
7. Implement in the smallest safe tranche.
8. Run repo-specific gates.
9. Run cross-repo, MCP, NetOps, security, FinOps, or emulated lab gates when
   required.
10. Parse stderr/test failures into `validation_errors`.
11. Route back to relevant role/remediation nodes.
12. Stop when approvals and gates pass, or when a circuit breaker requires
   human sign-off.
13. Produce PR summary, rollout notes, rollback plan, and NOC handoff metadata.
14. Post-deploy observation is handled by existing monitoring/NOC.

For `mixed` changes, all five senior role nodes run in parallel using native
LangGraph branching. Parallel state writes must use reducers so sibling branch
updates merge instead of overwriting each other.

## Graph State

The runtime state is centralized in `GraphState`:

```python
from typing import Annotated, Any, Dict, List, Literal, TypedDict
import operator

class GraphState(TypedDict):
    change_id: str
    change_class: Literal[
        "app_feature", "app_bugfix", "frontend", "cloud_api",
        "mcp_diagnostic_tooling", "noc_runtime", "infra_ansible",
        "routing_bgp_frr", "firewall_policy", "dns",
        "vault_secret_plane", "monitoring_logging", "mixed",
    ]
    risk_level: Literal["low", "medium", "high", "critical"]
    customer_impact: Literal["none", "possible", "expected"]

    source_of_truth_files: List[str]
    proposed_mutations: Dict[str, str]

    mcp_schema_breaking: bool
    emulated_lab_verified: Literal["not_applicable", "pending", "passed", "failed"]

    validation_errors: Annotated[List[Dict[str, Any]], operator.add]
    role_approvals: Dict[str, bool]
    retry_counters: Dict[str, int]

    rollback_plan: str
    noc_handoff_metadata: Dict[str, Any]
    requires_human_signoff: bool
```

The Python implementation uses an additional reducer for `role_approvals` so
parallel role approval updates merge safely.

Optional Phase 2 staging keys:

- `gate_commands`: explicit local commands to run without a shell.
- `gate_results`: captured command, return code, stdout, and stderr.
- `gate_status`: latest gate status, separate from append-only
  `validation_errors`.
- `prompt_artifacts`: loaded Markdown prompt text for future LLM role nodes.
- `approval_decision`: operator review status for persisted state artifacts.
- `llm_mock_responses`: test/operator-supplied structured role outputs.
- `llm_outputs`: append-only role review metadata.
- `workspace_root`: temporary mutation workspace path.
- `workspace_written_files`: relative files written into the temporary
  workspace.
- `workspace_cleaned_up`: whether the latest temporary workspace was removed.
- `handoff_output_dir`: explicit output directory for NOC metadata.
- `noc_handoff_path`: rendered `noc_handoff.json` path.
- `promotion_enabled`: whether branch-backed promotion is active.
- `promotion_repositories`: repo name to checkout path allowlist.
- `promotion_allowed_paths`: repo name to allowed relative path prefixes.
- `promotion_worktree_root`: parent directory for branch-backed worktrees.
- `promotion_branch_prefix`: branch namespace for promoted worktrees.
- `promotion_status`: latest promotion status.
- `promotion_results`: generated branch/worktree/diff metadata.
- `pr_enabled`: whether PR publication is enabled.
- `pr_status`: latest PR boundary status.
- `pr_remote`: remote used for branch push.
- `pr_create_github`: whether GitHub draft PR creation was requested.
- `commit_message`: commit message used for promoted worktrees.
- `pr_title`: PR title.
- `pr_body`: PR body.
- `pr_results`: pushed branch/commit/PR metadata.
- `policy_file`: optional policy file path override.
- `policy_status`: latest policy guard status.
- `repo_workspace_root`: parent directory for sibling repo discovery.
- `promotion_repo_names`: repo names to resolve through the repo adapter.
- `promotion_base_ref`: base ref that target repos must resolve.
- `repo_adapter_status`: latest repo adapter status.
- `repo_adapter_results`: discovered/verified repo metadata.

Because `validation_errors` is append-only history, routing decisions use the
latest `gate_status` to decide whether errors are currently active.

## LLM Hydration Contract

Role nodes combine:

- the role's Markdown system prompt;
- current contents of `source_of_truth_files`;
- the active `GraphState`;
- optional mock responses for tests.

The structured role output is:

```text
approved: bool
validation_errors: list[dict]
proposed_mutations: list[{path: str, content: str}]
notes: str
```

Mutation paths must be relative and must not contain `..`. The workspace writer
applies them only under a temporary directory.

Live inference failures must not crash the graph. They are mapped into role
review validation errors and retry counters so the graph can remediate or stop
at the human sign-off circuit breaker.

## NOC Handoff JSON

When a handoff directory is configured, the packaging node writes
`noc_handoff.json` with this structural shape:

```json
{
  "schema_version": 1,
  "generated_at": "ISO-8601 timestamp",
  "change": {
    "change_id": "...",
    "change_class": "...",
    "risk_level": "...",
    "customer_impact": "...",
    "mcp_schema_breaking": false,
    "emulated_lab_verified": "not_applicable"
  },
  "validation": {
    "gate_status": "passed",
    "error_count": 0,
    "retry_counters": {},
    "gate_results": []
  },
  "roles": {
    "approvals": {},
    "llm_outputs": []
  },
  "workspace": {
    "written_files": [],
    "cleaned_up": true
  },
  "promotion": {
    "status": "not_requested",
    "results": [],
    "requires_human_signoff": false
  },
  "rollback": {
    "plan": "...",
    "requires_human_signoff": false
  },
  "noc": {}
}
```

## Change Classes

- `app_feature`
- `app_bugfix`
- `frontend`
- `cloud_api`
- `mcp_diagnostic_tooling`
- `noc_runtime`
- `infra_ansible`
- `routing_bgp_frr`
- `firewall_policy`
- `dns`
- `vault_secret_plane`
- `monitoring_logging`
- `mixed`

## Role Matrix

Required defaults:

- `app_feature`, `app_bugfix`, `frontend`: Systems Engineer + DevOps/NetOps.
- `cloud_api`: Systems Engineer + FinOps Integrity; add DevOps/NetOps for
  deploy/runtime changes.
- `mcp_diagnostic_tooling`: Systems Engineer + DevOps/NetOps; add Security if
  diagnostic output may expose secrets or tenant data.
- `noc_runtime`: Systems Engineer + DevOps/NetOps + Security Auditor.
- `infra_ansible`, `dns`, `monitoring_logging`: Systems Engineer +
  DevOps/NetOps; add Network/Security as relevant.
- `routing_bgp_frr`: Network Architect + Security Auditor + emulated lab
  verification.
- `firewall_policy`: Network Architect + Security Auditor +
  `docs/network-flows.md` alignment + emulated lab verification.
- `vault_secret_plane`: Security Auditor + DevOps/NetOps.
- `mixed`: all five approval roles.

The graph cannot exit successfully until every required role approval is true,
required gates pass, and `requires_human_signoff` is false.

## Dynamic Remediation

Gate nodes append structured failures into `validation_errors` and increment
`retry_counters`.

- FinOps failures clear `finops_integrity`.
- Security failures clear `security_auditor`.
- Network/routing/firewall failures clear `network_architect`.
- Runtime/service failures clear `systems_engineer`.
- CI/CD/render/deploy/Vault rendering/drift failures clear `devops_netops`.

If any single retry counter reaches `3`, the graph sets
`requires_human_signoff = True`, exits the autonomous loop, and leaves the full
state available for manual triage.

## Operator CLI

Run the deterministic graph and persist a state artifact:

```bash
hyrule-engineering-loop run APP_BUGFIX_GREEN app_bugfix
```

Run one explicit local gate command:

```bash
hyrule-engineering-loop run TYPECHECK app_bugfix --gate-command uv run mypy src
```

Inspect or approve persisted state:

```bash
hyrule-engineering-loop show APP_BUGFIX_GREEN
hyrule-engineering-loop approve APP_BUGFIX_GREEN
```

Render a NOC handoff file while running:

```bash
hyrule-engineering-loop run APP_BUGFIX_GREEN app_bugfix --handoff-dir /tmp/handoff
```

Promote validated `repo:path` mutations into branch-backed worktrees:

```bash
hyrule-engineering-loop run APP_BUGFIX_GREEN app_bugfix \
  --promotion-enabled \
  --promotion-repo hyrule-cloud=/home/svag/Dev/hyrule-cloud \
  --promotion-allow hyrule-cloud=hyrule_cloud \
  --promotion-worktree-root /tmp/hyrule-loop-worktrees
```

Publish an approved promoted worktree branch:

```bash
hyrule-engineering-loop approve APP_BUGFIX_GREEN
hyrule-engineering-loop pr APP_BUGFIX_GREEN \
  --remote origin \
  --commit-message "Apply engineering loop tranche" \
  --title "Apply engineering loop tranche" \
  --body "Generated by the Hyrule Engineering Loop."
```

Run a non-publishing end-to-end dry run against a sibling repo:

```bash
hyrule-engineering-loop dry-run SMOKE_DOC app_bugfix \
  --repo-workspace-root /home/svag/Dev \
  --promotion-repo-name hyrule-cloud \
  --promotion-allow hyrule-cloud=docs \
  --promotion-worktree-root /tmp/hyrule-loop-worktrees \
  --mutation "hyrule-cloud:docs/smoke.md=hello"
```

Run the offline operator harness, including approval, branch push, mocked PR
creation, and NOC handoff rendering:

```bash
hyrule-engineering-loop operator-dry-run \
  --root /tmp/hyrule-loop-operator \
  --label engineering-loop \
  --reviewer netops-review
```

Run a docs-only canary against a real sibling repo without publishing:

```bash
hyrule-engineering-loop sibling-canary \
  --workspace-root /home/svag/Dev \
  --repo-name hyrule-cloud \
  --output-root /tmp/hyrule-loop-canary
```

Run the loop from a feature request file:

```bash
uv run hyrule-engineering-loop feature ADD_PAYMENT_RETRY \
  --request /tmp/add-payment-retry.md \
  --repo hyrule-cloud \
  --workspace-root /home/svag/Dev \
  --output-root /tmp/hyrule-feature-add-payment-retry \
  --allow docs \
  --source README.md
```

In default mock mode this creates a promoted planning artifact under
`docs/engineering-loop/`. To practice explicit file output without live LLMs,
add `--mock-mutation "docs/example.md=example content"`. To let role nodes
propose structured mutations from a configured provider, set `HYRULE_MOCK_LLM=0`
and provide the required LLM environment variables.

The feature command writes `loop_trace.json` beside `noc_handoff.json`. Inspect
the trace to see how state flowed through role review, gates, policy, promotion,
and packaging without exposing full source contents or diffs.

From Pi, use the global extension command:

```text
/loop Add checkout progress indicators
```

When no arguments are supplied, `/loop` opens a single menu for starting a new
request, inspecting the latest trace, cleaning up the latest worktree, or
approving the latest state artifact.

The CLI is an operator boundary, not a production deploy tool.

## MCP Compatibility

`mcp_schema_breaking` defaults to false. Any changed diagnostic output, command
syntax, log structure, or tool schema that affects `hyrule-mcp` or
`hyrule-noc-agent` requires coordinated multi-repo planning. The NOC Agent must
not be blinded by uncoordinated telemetry/schema changes.

## NOC Boundary Rules

NOC Agent is production runtime infrastructure, not the development
orchestrator.

The engineering loop may:

- read NOC docs and golden-state context;
- modify `noc-agent` through normal PRs;
- include expected deploy impact in PR/deployment notes;
- ask for read-only production telemetry when validating infra changes.

The engineering loop must not:

- run inside `noc-agent`;
- use `noc-agent` incident state as development state;
- ask `noc-agent` to spawn coding agents;
- bypass CI, PR, or deploy approval gates.

NOC Agent may:

- detect deploy-caused regressions;
- correlate alerts to recent deploy metadata;
- recommend rollback;
- verify runtime recovery.

NOC Agent must not:

- implement feature work;
- author PRs as incident remediation;
- approve its own production mutations.

## Break-Glass Rollback Handshake

Every deployable engineering tranche must define post-deploy health metrics,
an observation window, rollback trigger, and deterministic rollback
command/workflow. If NOC observes those metrics breaching inside the window, it
may trigger only the pre-approved rollback workflow. It still must not write
code, generate PRs, or approve production mutations.

## Rollout Notes Template

Every non-trivial PR should include:

```md
## Change class

## Repos touched

## Senior role reviews

## Source-of-truth files consulted

## Validation gates run

## Expected production impact

## Rollback plan

## NOC handoff

## Post-deploy checks
```

For infra/routing/firewall changes, NOC handoff must include:

```md
- expected alerts:
- expected duration:
- affected hosts/services:
- rollback trigger:
- operator command/workflow:
```
