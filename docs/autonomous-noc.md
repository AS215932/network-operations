# Autonomous NOC Operations

This document covers the post-refactor AS215932 NOC stack deployed on `noc`.
It complements the lower-level Ansible and deployment runbooks.

## Components

| Component | Purpose |
| --- | --- |
| `noc-agent` | FastAPI ingress, LangGraph orchestration, incident summaries, approval state |
| Redis | Local checkpoint and incident-memory store |
| `hyrule-mcp` | Supervised local MCP daemon providing diagnostic telemetry |
| Discord bot | Interactive operator console for investigations and decisions |
| `nocctl` | SSH/VPN-safe local fallback CLI |

The current rollout is diagnostic-first. The system produces reviewable
proposals and records human decisions. It does not execute infrastructure
changes yet.

## Investigation lifecycle

1. Alertmanager or Icinga posts to `noc-agent`.
2. The graph normalizes the alert, deduplicates overlapping symptoms, and
   recalls recent incident history.
3. The supervisor routes to a specialist posture: BGP, firewall/security, or
   infrastructure.
4. Specialist reasoning is checked against evidence rules and the golden-state
   manifest.
5. A proposal is written into incident state and marked waiting for review.
6. Discord or `nocctl` records the operator decision.

Repeated incidents are marked chronic after more than three correlated events
inside a rolling 24-hour window.

## Golden state and drift

`noc-agent` carries two linked assets:

- a curated supervisor prompt artifact
- a machine-readable golden-state manifest

The prompt explains operational discipline. The manifest stores intended-state
anchors that can be compared against live MCP telemetry. This keeps the model
close to repo-defined truth while still using real-time diagnostics.

## Control-plane access

Primary operator flow:

- Discord slash commands and mention-driven investigations
- Discord approval/rejection/status flows

Fallback flow during chat or upstream outages:

```bash
ssh noc
nocctl pending
nocctl show <incident-id>
nocctl decide <incident-id> rejected --operator svag --comment "hold for manual work"
```

The fallback talks only to the loopback control API on `noc`.

## MCP daemon model

`hyrule-mcp.service` runs locally on `noc` and exposes loopback streamable HTTP
at `http://127.0.0.1:8765/mcp`. This daemon model avoids tying long-running
diagnostics to the `noc-agent` worker lifecycle.

The daemon:

- keeps structured stdout/stderr/exit metadata
- short-circuits self-inspection locally
- uses SSH fan-out for remote hosts
- caps heavyweight captures and burst probes
- prefers Icinga REST and Prometheus APIs over shell scraping

## Deployment inputs

Rendered NOC environment now includes:

- `NOC_REDIS_URL`
- `HYRULE_MCP_URL`
- `NOC_CONTROL_TOKEN`
- `NOC_APPROVAL_SIGNING_SECRET`
- Discord bot token and allowlist variables

The Vault-rendered environment template carries the same control-plane secrets.
Vault Agent is the production default for NOC deploys; the local plaintext
bootstrap file is used only to seed or rotate Vault entries.

## Validation

Application repos keep hermetic regression suites:

```bash
cd /opt/noc-agent
uv run --group dev python -m pytest -q

cd /opt/hyrule-mcp
uv run --group dev python -m pytest -q
```

Live smoke suites exist but are explicitly opt-in and read-only.
