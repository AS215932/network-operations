# Hyrule Engineering Loop

Autonomous development loop for the AS215932 (Hyrule / Servify) infrastructure.
A LangGraph runtime that classifies a change, plans it into a task spec,
delegates implementation to a real coding-agent backend inside a guarded
worktree, re-runs gates, has senior-role agents judge the resulting diff,
learns from every run, and stops at a **draft PR** for human sign-off. Merges
and production applies are always human-gated.

Extracted from `AS215932/network-operations` (history preserved) once the v2
refactor stabilized — see that repo's `docs/engineering-loop/` for the design
spec and roadmap, and `docs/agentic-development-loop.md` here for the runtime.

## Layout

- `src/hyrule_engineering_loop/` — the LangGraph runtime, `AgentBackend`,
  policy/judgment/memory/intake/daemon modules, and the operator CLI.
- `tests/` — the phased test suites (`test_engineering_graph.py`,
  `test_phase*.py`), fully offline (mock backend, no API keys).
- `skills/` — role, writer, and ISP-procedure skills the loop injects.
- `docs/agent-loops/`, `docs/agentic-development-loop.md`,
  `docs/engineering-loop/` — role cards, runtime reference, and v2 design.
- `integrations/pi/` — the Pi `/loop` extension.
- `configs/loop/` — systemd service + timer for the operations lane.
- `model-policy.yml`, `engineering-loop-policy.yml` — model/backend routing
  and the mutation/publication policy guards.

## Develop

```bash
uv run --group dev python -m pytest -q
uv run --group dev mypy --strict src
uvx ruff check src tests
```

## Run

```bash
uv run hyrule-engineering-loop --help
# one operations-lane cycle over the loop:approved queue:
uv run hyrule-engineering-loop daemon --once
```

## Safety

The backend executes generated code. CI runs only on the unprivileged
`ci-pr` runner (label `hyrule-public-pr`); the daemon refuses to run when
`GITHUB_ACTIONS` is set. Never schedule it on a privileged runner.
