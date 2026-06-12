"""Memory — the self-improvement flywheel for the Hyrule Engineering Loop.

Phase D of the v2 architecture (``docs/engineering-loop/v2-architecture.md``
§6). The tree:

```text
memory/
  lessons/<repo>.md      # accumulated rules, AGENTS.md-style, human-curated
  proposals/<change-id>.md  # loop-proposed lesson edits, await human merge
  journal/<change-id>.md # per-run lab notes: attempts, failures, findings, cost
```

The loop *reads* lessons and the journal tail from the configured memory
root (falling back to the loop repo's own ``memory/`` tree for the
human-curated rulebook), but *writes* — journal entries and lesson
proposals — only when a memory root is explicitly configured
(``GraphState["memory_dir"]`` or ``HYRULE_MEMORY_DIR``). The loop never
writes ``memory/lessons/`` directly: humans merge proposals, and recurring
failure classes graduate from lessons into deterministic gates or policy
rules (the ratchet), after which the lesson is retired.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.state import GraphState


def _loop_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

MAX_LESSONS_CHARS = 8_000
MAX_JOURNAL_TAIL_ENTRIES = 2
MAX_JOURNAL_TAIL_CHARS = 4_000


def resolve_memory_write_root(state: GraphState) -> Path | None:
    """Return the configured memory root for writes, or None when unset."""
    raw = state.get("memory_dir") or os.environ.get("HYRULE_MEMORY_DIR")
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def resolve_memory_read_root(state: GraphState) -> Path:
    """Return the memory root for reads; the loop repo's tree is the fallback."""
    return resolve_memory_write_root(state) or _loop_repo_root() / "memory"


def load_lessons_for_repo(state: GraphState, repo: str) -> str | None:
    """Return ``lessons/<repo>.md`` (clipped) from the read root, if present."""
    path = resolve_memory_read_root(state) / "lessons" / f"{repo}.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")[:MAX_LESSONS_CHARS]


def journal_tail_for_repo(state: GraphState, repo: str) -> str | None:
    """Return the most recent journal entries mentioning ``repo`` (clipped)."""
    journal_dir = resolve_memory_read_root(state) / "journal"
    if not journal_dir.is_dir():
        return None
    entries: list[tuple[float, Path]] = []
    for path in journal_dir.glob("*.md"):
        if path.stem == state["change_id"]:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        mentions_repo = any(
            repo in line
            for line in text.splitlines()
            if line.startswith(("- repo:", "- repos:"))
        )
        if mentions_repo:
            entries.append((path.stat().st_mtime, path))
    if not entries:
        return None
    entries.sort(reverse=True)
    parts: list[str] = []
    for _, path in entries[:MAX_JOURNAL_TAIL_ENTRIES]:
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n---\n\n".join(parts)[:MAX_JOURNAL_TAIL_CHARS]


def memory_context_for_state(state: GraphState) -> dict[str, Any]:
    """Compact lessons/journal availability summary for planner context."""
    repos = sorted(
        set(state.get("promotion_repositories", {}))
        | set((state.get("task_spec") or {}).get("repos", {}))
    )
    context: dict[str, Any] = {}
    for repo in repos:
        lessons = load_lessons_for_repo(state, repo)
        tail = journal_tail_for_repo(state, repo)
        context[repo] = {
            "lessons_chars": len(lessons) if lessons else 0,
            "journal_tail_chars": len(tail) if tail else 0,
        }
    return context


def detect_failure_patterns(state: GraphState) -> list[dict[str, Any]]:
    """Detect recurring failure classes worth proposing a lesson for.

    The signature pattern (roadmap § D AC2): the same gate command failing
    twice or more in one run. Repeated backend failures and repeated
    judgment rejections are reported the same way.
    """
    patterns: list[dict[str, Any]] = []

    failures_by_command: dict[str, list[dict[str, Any]]] = {}
    for result in state.get("gate_results", []):
        if not isinstance(result, dict) or result.get("returncode") in (0, None):
            continue
        command = result.get("command")
        if isinstance(command, list):
            name = " ".join(str(part) for part in command)
        else:
            name = str(command)
        failures_by_command.setdefault(name, []).append(result)
    for name, failures in sorted(failures_by_command.items()):
        if len(failures) >= 2:
            patterns.append(
                {
                    "kind": "gate",
                    "name": name,
                    "count": len(failures),
                    "excerpt": str(failures[-1].get("stderr", ""))[:600],
                }
            )

    counters = state["retry_counters"]
    if counters.get("backend", 0) >= 2:
        backend_errors = [
            str(error.get("message", ""))
            for error in state["validation_errors"]
            if isinstance(error, dict) and error.get("node") == "delegate_implementation"
        ]
        patterns.append(
            {
                "kind": "backend",
                "name": state.get("backend_name", "backend"),
                "count": counters["backend"],
                "excerpt": (backend_errors[-1] if backend_errors else "")[:600],
            }
        )
    if counters.get("judgment", 0) >= 2:
        finding_messages = [
            str(finding.get("message", ""))
            for finding in state.get("remediation_findings") or []
            if isinstance(finding, dict)
        ]
        patterns.append(
            {
                "kind": "judgment",
                "name": "role judgment rejections",
                "count": counters["judgment"],
                "excerpt": (finding_messages[-1] if finding_messages else "")[:600],
            }
        )
    return patterns


def _target_repos(state: GraphState) -> list[str]:
    return sorted(
        set(state.get("promotion_repositories", {}))
        | set((state.get("task_spec") or {}).get("repos", {}))
    ) or ["(no target repo)"]


def render_journal_entry(state: GraphState, patterns: list[dict[str, Any]]) -> str:
    """Render the per-run lab notes: attempts, failures, findings, cost."""
    backend_runs = state.get("backend_results", [])
    total_iterations = sum(
        int(run.get("iterations", 0)) for run in backend_runs if isinstance(run, dict)
    )
    cost_usd = sum(
        float(run.get("cost", {}).get("usd") or 0.0)
        for run in backend_runs
        if isinstance(run, dict)
    )
    verdicts = [
        f"{entry.get('role')}: {entry.get('verdict')}"
        for entry in state.get("judgment_results", [])
        if isinstance(entry, dict) and entry.get("phase") == "judgment"
    ]
    lines = [
        f"# Journal: {state['change_id']}",
        "",
        f"- generated_at: {datetime.now(UTC).isoformat()}",
        f"- change_class: {state['change_class']}; risk: {state['risk_level']}",
        f"- repos: {', '.join(_target_repos(state))}",
        f"- gate_status: {state.get('gate_status', 'not_run')}; "
        f"policy_status: {state.get('policy_status', 'not_run')}; "
        f"promotion_status: {state.get('promotion_status', 'not_requested')}",
        f"- signoff_status: {state.get('signoff_status', 'not_required')}",
        f"- retry_counters: {dict(state['retry_counters']) or '{}'}",
        f"- backend_runs: {len(backend_runs)}; iterations: {total_iterations}; "
        f"cost_usd: {cost_usd:.4f}",
        f"- validation_errors: {len(state['validation_errors'])}",
    ]
    if verdicts:
        lines.append(f"- judgment: {', '.join(verdicts)}")
    if state.get("task_spec_path"):
        lines.append(f"- task_spec: {state['task_spec_path']}")
    if state.get("loop_trace_path"):
        lines.append(f"- trace: {state['loop_trace_path']}")
    if patterns:
        lines.extend(["", "## Failure patterns", ""])
        for pattern in patterns:
            lines.append(
                f"- {pattern['kind']}: {pattern['name']} (x{pattern['count']})"
            )
    if state["validation_errors"]:
        lines.extend(["", "## Last errors", ""])
        for error in state["validation_errors"][-3:]:
            if isinstance(error, dict):
                lines.append(f"- {error.get('domain')}: {str(error.get('message', ''))[:200]}")
    lines.append("")
    return "\n".join(lines)


def write_journal_entry(
    state: GraphState, root: Path, patterns: list[dict[str, Any]]
) -> str:
    """Write exactly one journal entry per run (re-runs overwrite their entry)."""
    path = root / "journal" / f"{state['change_id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_journal_entry(state, patterns), encoding="utf-8")
    return str(path)


def render_lesson_proposal(state: GraphState, patterns: list[dict[str, Any]]) -> str:
    """Render a lesson proposal; every entry traces to this run's failures."""
    repos = _target_repos(state)
    lines = [
        f"# Lesson proposal: {state['change_id']}",
        "",
        f"- generated_at: {datetime.now(UTC).isoformat()}",
        f"- target lessons file(s): "
        + ", ".join(f"memory/lessons/{repo}.md" for repo in repos),
        "",
        "The loop never edits its own rulebook: review, edit, and merge the",
        "proposed entries into the lessons file as a normal git change — or",
        "graduate the failure class into a deterministic gate or policy rule",
        "and retire the lesson (the ratchet).",
        "",
    ]
    for pattern in patterns:
        lines.extend(
            [
                f"## {pattern['kind']}: {pattern['name']}",
                "",
                f"Observed {pattern['count']}x in `{state['change_id']}`.",
                "",
                "Proposed lesson entry:",
                "",
                f"- When working on {', '.join(repos)}: avoid repeating the "
                f"failure `{pattern['name']}` — it failed {pattern['count']}x "
                f"in {state['change_id']}."
                f" (traced to memory/journal/{state['change_id']}.md)",
                "",
            ]
        )
        if pattern.get("excerpt"):
            lines.extend(["Failure excerpt:", "", "```text", str(pattern["excerpt"]), "```", ""])
    return "\n".join(lines)


def write_lesson_proposal(
    state: GraphState, root: Path, patterns: list[dict[str, Any]]
) -> str | None:
    """Write a lesson proposal into ``proposals/`` — never into ``lessons/``."""
    if not patterns:
        return None
    path = root / "proposals" / f"{state['change_id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_lesson_proposal(state, patterns), encoding="utf-8")
    return str(path)


def list_memory(root: Path) -> dict[str, Any]:
    """Operator review surface: lessons files and pending proposals."""

    def _entries(directory: Path) -> list[dict[str, Any]]:
        if not directory.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            items.append(
                {
                    "name": path.stem,
                    "path": str(path),
                    "chars": len(text),
                    "excerpt": text[:400],
                }
            )
        return items

    return {
        "root": str(root),
        "lessons": _entries(root / "lessons"),
        "proposals": _entries(root / "proposals"),
        "journal_count": len(list((root / "journal").glob("*.md")))
        if (root / "journal").is_dir()
        else 0,
    }
