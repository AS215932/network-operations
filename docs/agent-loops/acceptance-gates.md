# Acceptance Gates

The Hyrule Engineering Loop must select gates based on changed repos and change
class. Production apply is never automatic from the development loop.

## hyrule-infra

Docs-only:

```bash
git diff --check
```

Ansible/config:

```bash
cd ansible
ansible-playbook playbooks/firewall.yml --tags validate --connection=local --skip-tags snapshot
```

Broader infra uses existing CI workflows:

- `render-check.yml`
- `iac-tests.yml`
- `netops-nightly.yml`
- `drift-detection.yml`
- manual `apply.yml` with production environment approval

## hyrule-cloud

```bash
uv run --group dev python -m pytest -q
uv run --group dev ruff check .
uv run --group dev mypy hyrule_cloud
```

## hyrule-web

```bash
uv run --group dev python -m pytest -q
npm run check
```

## hyrule-mcp

```bash
uv run --group dev python -m pytest -q
```

Live smoke is opt-in only:

```bash
HYRULE_MCP_LIVE_SMOKE=1 uv run --group dev python -m pytest -q tests/test_live_smoke.py
```

## hyrule-noc-agent

```bash
uv run --group dev python -m pytest -q
```

Live smoke is opt-in only:

```bash
NOC_AGENT_LIVE_SMOKE=1 uv run --group dev python -m pytest -q tests/test_live_smoke.py
```

## MCP Contract Enforcement

If an engineering change alters diagnostic output, command syntax, log shape,
or MCP tool schema, set `mcp_schema_breaking = true`. The change then requires
coordinated planning with `hyrule-mcp` so NOC incident diagnostics are not
blinded by schema drift.

## Digital Twin / Local Emulation

Required for:

- `routing_bgp_frr`
- `firewall_policy`
- high-risk OS/runtime changes

Use trusted lab tooling where available, such as Batfish, Containerlab, or
nested local hypervisor validation. The gate must verify native target config
parsing, routing convergence or firewall isolation, expected failure behavior,
and rollback execution.

## Break-Glass Rollback

Deploy notes must define:

- post-deploy health metrics;
- observation window;
- rollback trigger;
- deterministic rollback command/workflow;
- affected hosts/services.

If those specific metrics breach, NOC may trigger only the pre-approved
rollback workflow. NOC must not write code, generate PRs, or approve its own
production mutations.

## Phase 2 Local Gate Runner

The runtime skeleton can execute explicit local validation commands supplied in
`GraphState["gate_commands"]`. Commands are run without a shell and capture:

- command argv;
- return code;
- stdout;
- stderr.

Failed commands append structured `validation_errors` with domain `ci` and
increment the `ci` retry counter. This is the staging point for later Batfish,
Containerlab, nested hypervisor, and repo-specific command adapters.

## Phase 3 Mutation Workspace

Structured LLM role outputs may include file mutations shaped as relative path
plus complete file content. The runtime applies those mutations to an isolated
temporary workspace immediately before the gate runner executes. Gate commands
run with that workspace as their current directory.

The workspace writer rejects absolute paths and `..` traversal. Cleanup runs
after gate execution and before remediation or PR packaging, so generated files
do not leak into the repository working tree.

Set `HYRULE_WORKSPACE_ROOT` to place temporary mutation workspaces under a
specific local staging directory. This is the safe handoff point for later
copying or targeting sibling repository checkouts.

## Phase 4 NOC Handoff

Set `HYRULE_HANDOFF_DIR` or `GraphState["handoff_output_dir"]` to render a
`noc_handoff.json` file during PR packaging. The file contains change metadata,
validation status, retry counters, role approvals, workspace cleanup status,
rollback plan, and NOC handoff metadata.

The handoff file is data for the production monitoring/NOC plane. It is not a
development command channel, and it must not cause NOC Agent to spawn coding
agents or author PRs.

## Phase 5 Worktree Promotion

Validated mutations can be promoted into sibling repositories only when
promotion is explicitly enabled. Mutation keys must use `repo:path` format, and
the repo plus path must both pass allowlists:

- `promotion_repositories`: repo name to checkout path.
- `promotion_allowed_paths`: repo name to allowed relative path prefixes.
- `promotion_worktree_root`: parent directory for generated worktrees.
- `promotion_branch_prefix`: branch namespace.

The promotion stage creates a git worktree from `HEAD`, writes the allowed
mutations, runs `git add -N .`, and captures `git diff -- .` into
`promotion_results`. It does not commit, push, open PRs, or mutate production.

If promotion fails partway through, created worktrees and branches are removed.
If promotion succeeds, the worktree is left in place for human inspection and
`requires_human_signoff` is set before any future push/PR phase can run.

## Phase 6 PR Boundary

PR publication is separate from graph execution. The operator must approve the
persisted state artifact before publication:

```bash
hyrule-engineering-loop approve <change-id>
hyrule-engineering-loop pr <change-id> \
  --remote origin \
  --commit-message "Apply validated tranche" \
  --title "Apply validated tranche" \
  --body "Generated by the Hyrule Engineering Loop."
```

The PR boundary refuses to run without `approval_decision: approved` and
without `promotion_results`. It commits inside each promoted worktree and pushes
the generated branch to the configured remote. GitHub draft PR creation is
disabled by default and must be explicitly requested later with the dedicated
flag/environment gate.

## Phase 7 Policy Guards

Policy lives in `engineering-loop-policy.yml` by default. Override with
`HYRULE_POLICY_FILE` or `GraphState["policy_file"]`.

The policy guard runs after temporary workspace cleanup and before promotion.
It validates:

- denied mutation path globs, including secret-looking paths;
- denied content patterns, including private keys and token assignments;
- maximum changed file count;
- maximum file size;
- allowed gate command names;
- protected promotion branch prefixes;
- repo-root allowlists;
- NOC handoff output directory allowlists.

The PR boundary also checks the remote against `allowed_pr_remotes` before any
commit or push. Policy failures append structured `validation_errors`, set
`policy_status: failed`, and route to human sign-off.

## Phase 8 Repo Adapter Dry Run

The repo adapter normalizes sibling repo names into checked-out paths before
policy and promotion. It verifies:

- the repo exists and has a `.git` directory;
- the repo is on an attached branch, not detached HEAD;
- `git status --porcelain` is clean;
- the configured base ref resolves.

Use `dry-run` for non-publishing end-to-end validation:

```bash
hyrule-engineering-loop dry-run <change-id> <change-class> \
  --repo-workspace-root /home/svag/Dev \
  --promotion-repo-name hyrule-cloud \
  --promotion-allow hyrule-cloud=docs \
  --promotion-worktree-root /tmp/hyrule-loop-worktrees \
  --mutation "hyrule-cloud:docs/smoke.md=hello"
```

`dry-run` never approves, commits, pushes, or creates PRs.
