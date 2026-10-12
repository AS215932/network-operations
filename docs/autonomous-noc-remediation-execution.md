# Autonomous NOC Remediation Execution Tranche

## Context

The Autonomous NOC tranche currently deployed is diagnostic-first. It can ingest alerts, correlate incidents, route work through specialist graph nodes, collect MCP-backed evidence, produce structured proposals, pause for human approval, and record operator decisions. It does not yet execute approved infrastructure remediation.

This document is the issue body for the next tranche: safe, auditable, approved remediation execution using a commit-confirm style rollback pattern for AS215932 infrastructure.

## Current Deployed State

- `hyrule-noc-agent` has LangGraph-style orchestration primitives and structured incident/proposal models.
- Redis-backed state/checkpoint plumbing is deployed on `noc`.
- Human approval/resume plumbing exists for tranche 1 approval recording.
- Discord webhook notifications are deployed.
- Local fallback control-plane plumbing exists conceptually, but remediation execution is disabled.
- `hyrule-mcp` is deployed as a supervised local daemon on `noc` using streamable HTTP.
- MCP tools return structured command envelopes with stdout, stderr, exit code, duration, and SSH error fields where applicable.
- Expanded diagnostic MCP tools are present for read-only investigation.
- Icinga access has moved toward API-first behavior.
- Vault Agent renders `/opt/noc-agent/.env`; local plaintext NOC secrets have been moved out of the local secret file.
- Live smoke checks for NOC Agent health and MCP daemon health pass against production.

### Execution gating (current posture)

Approved-remediation execution is now flag-gated in `noc-agent` and routed through `hyrule-mcp`:

| Flag (in `/opt/noc-agent/.env`) | Value | Effect |
|---|---|---|
| `HYRULE_MCP_ENABLE_ACTIONS` | `1` | MCP real-action tools (restart/ack) available, allowlist-scoped |
| `NOC_ENABLE_APPROVED_EXECUTION` | `1` | noc-agent executes approved remediation (off → `execution_disabled`) |
| `NOC_ENABLE_NOOP_ROLLBACK_GUARDS` | `0` | when `1`, execution routes through inert no-op rollback guards instead of real actions |
| `HYRULE_MCP_ENABLE_NOOP_GUARDS` | `0` | when `1`, exposes the MCP `prepare_commit_confirm`/`confirm_change`/`rollback_change`/`get_pending_rollback_guards` tools |

The no-op rollback guard substrate (hyrule-mcp#20, noc-agent#19) ships **dormant**: to exercise the inert prepare→confirm/rollback path without real mutation, flip `HYRULE_MCP_ENABLE_NOOP_GUARDS=1` and `NOC_ENABLE_NOOP_ROLLBACK_GUARDS=1` (real actions can stay enabled or be turned off independently), smoke `prepare_commit_confirm` + cancel against `noc`, then revert.

## Goal

Allow the NOC graph to execute only human-approved remediation plans with automatic rollback protection, full evidence retention, and post-change verification.

The execution system must be safe for sovereign ISP operations: no blind mutation, no unbounded commands, no one-way changes without rollback, and no dependence on Discord as the only approval path.

## Non-Goals

- Do not allow autonomous unapproved remediation.
- Do not implement broad arbitrary shell mutation as the primary interface.
- Do not remove human approval requirements.
- Do not make Discord the sole approval or recovery mechanism.
- Do not bypass Vault for execution credentials or approval secrets.

## Required Architecture

### 1. Remediation Proposal Model

Extend `ChangeProposal` or add a sibling execution model with fields suitable for deterministic execution:

- `proposal_id`
- `incident_id`
- `resource_id`
- `target_hosts`
- `risk_level`
- `blast_radius`
- `requires_commit_confirm`
- `pre_checks`
- `rollback_plan`
- `execution_steps`
- `post_checks`
- `expected_state`
- `timeout_seconds`
- `operator_approval`
- `approval_source`
- `approval_actor`
- `approval_timestamp`
- `execution_state`
- `execution_log_refs`

Every mutative step should reference a pre-defined MCP tool or a tightly constrained command template. Avoid arbitrary agent-authored shell by default.

### 2. Graph Nodes

Add or complete these graph stages after `approval_breakpoint`:

- `prepare_execution`
- `install_rollback_guard`
- `execute_remediation`
- `verify_remediation`
- `confirm_or_rollback`
- `finalize_execution`

`prepare_execution` must validate approval, operator authorization, proposal freshness, and current incident state.

`install_rollback_guard` must install a host-local automatic rollback action before mutation and record the guard ID/deadline in Redis.

`execute_remediation` must execute bounded, typed remediation steps via MCP and stream structured records into Redis.

`verify_remediation` must run direct post-checks through MCP. For routing changes, verify BGP session state, prefix acceptance/advertisement, and dataplane probes. For service changes, verify service health and recent logs.

`confirm_or_rollback` must cancel the rollback guard on success and preserve or invoke rollback on failure or timeout.

`finalize_execution` must produce an operator-facing summary with commands/tools used, before/after evidence, rollback state, residual risk, and follow-up.

### 3. MCP Execution Primitives

Implement explicit mutative tools with hard guardrails:

- `prepare_commit_confirm(host, rollback_script, delay_seconds)`
- `confirm_change(host, guard_id)`
- `rollback_change(host, guard_id)`
- `get_pending_rollback_guards(host)`
- `apply_config_candidate(host, subsystem, candidate, mode=dry_run|apply)`
- `restart_service_guarded(host, unit, guard_id, post_check)`

Implementation notes:

- Use `systemd-run --on-active=...` or `at now + ...` on Linux.
- Use `at`, `daemon`, or a small installed rollback helper on FreeBSD/OpenBSD depending on host capabilities.
- Generate rollback scripts from deterministic templates, not unconstrained LLM shell.
- Store rollback scripts and logs under `/var/lib/hyrule-mcp/rollback/` with strict permissions.
- Include `proposal_id`, `incident_id`, `approved_by`, and `approval_timestamp` in every mutative call.
- Preserve the structured command envelope: `stdout`, `stderr`, `exit_code`, `duration_ms`, `ssh_error`.
- Keep mutative command blocklists on raw fallback tools.

### 4. Local Fallback Approval Path

Complete `nocctl` support so operators can approve and resume when Discord or external connectivity is impaired.

Required commands:

- `nocctl incidents list --pending`
- `nocctl incidents show <incident_id>`
- `nocctl proposals show <proposal_id>`
- `nocctl approve <proposal_id> --comment ...`
- `nocctl reject <proposal_id> --comment ...`
- `nocctl resume <incident_id>`
- `nocctl execution show <execution_id>`

Security requirements:

- Loopback-only control API on `noc`.
- Authentication token stored in Vault and rendered by Vault Agent.
- Operator identity recorded from local auth context where possible.
- Refuse remote non-loopback approval unless explicitly configured.

### 5. Discord Approval Path

When `DISCORD_BOT_TOKEN` is present in Vault and allowlists are configured, the bot should support approval controls.

Required behavior:

- Only approved guilds/channels/roles can approve.
- Button/slash command approval must include a required operator comment for high-risk changes.
- Bot should show proposal summary, risk, rollback guard plan, timeout, and post-check plan before approval.
- Bot outage must not block local CLI approval.

### 6. Evidence and Safety Rules

Before execution, validators must enforce:

- No remediation without at least one approved `ChangeProposal`.
- No high-confidence claim without direct evidence, per current evidence discipline.
- No mutative action if the proposal has unresolved contradictory evidence.
- No action when drift findings imply the proposed change would diverge further from golden state, unless explicitly approved.
- No execution if rollback guard installation fails for changes marked `requires_commit_confirm`.
- No execution from stale telemetry; pre-check evidence must be fresh.

### 7. Test Plan

Add hermetic tests first:

- Proposal cannot execute without approval.
- Approval from unauthorized Discord role is rejected.
- Local CLI approval succeeds when Discord is unavailable.
- Stale approval requires refresh.
- Rollback guard is installed before mutation.
- Mutation is refused if rollback guard installation fails.
- Verification success calls `confirm_change`.
- Verification failure leaves rollback guard active or calls rollback.
- Timeout path records rollback pending/fired.
- Raw SSH fallback refuses mutative commands.
- Execution audit includes operator identity and proposal ID.
- Restart-service scenario uses guarded execution.
- Config-candidate dry run must pass before apply.
- Redis resume recovers pending execution state after `noc-agent` restart.
- MCP daemon restart/reconnect does not duplicate execution steps.

Add opt-in live smoke tests:

- Read pending rollback guards on a safe test host.
- Install and cancel a harmless rollback guard in a dedicated test namespace.
- Execute a no-op guarded change against a disposable local target only.
- Confirm Vault-rendered approval secret exists without printing it.

### 8. Suggested Implementation Order for an LLM Agent

1. Read current `hyrule-noc-agent` graph, approval, Redis, and proposal models.
2. Read current `hyrule-mcp` transport, command envelope, and resource limit modules.
3. Add execution models and tests in `hyrule-noc-agent` without wiring mutation yet.
4. Add MCP rollback guard primitives with hermetic subprocess/SSH fakes.
5. Wire `prepare_execution -> install_rollback_guard -> execute_remediation -> verify_remediation -> confirm_or_rollback -> finalize_execution` behind a feature flag.
6. Implement `nocctl` approval and execution inspection commands.
7. Implement Discord bot approval controls once token/allowlists are in Vault.
8. Add Redis resume tests for paused and in-flight executions.
9. Add live smoke tests limited to no-op or disposable targets.
10. Deploy with execution feature flag disabled, run tests, then enable only for low-risk allowlisted remediations.

## Acceptance Criteria

- All existing characterization tests continue to pass.
- New execution tests cover success, rejection, rollback, timeout, and resume paths.
- No mutative remediation can run without recorded human approval.
- Every mutative action has an installed rollback guard unless explicitly classified as non-rollback-needed.
- Verification success cancels rollback guard.
- Verification failure or timeout preserves or triggers rollback.
- Operator-facing final summary includes evidence, action log, before/after state, approval actor, and rollback outcome.
- Discord approval works when configured, but local `nocctl` approval works without Discord.
- Vault is the only source for approval/API/bot secrets.
- Documentation explains how to operate, test, disable, and recover the remediation execution tranche.

## Rollout Recommendation

Start with a narrow allowlist:

- Restart a known safe non-critical service on a test host.
- Apply a no-op candidate config in dry-run mode.
- Install/cancel rollback guards without mutation.

Only after repeated successful live smoke runs should this expand to production-affecting network remediations such as FRR, PF/nftables, WireGuard, or interface operations.
