"""Local command gate execution for the engineering loop."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

MAX_OUTPUT_CHARS = 8_000


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n[output truncated]"


def _as_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def run_gate_commands(
    commands: Iterable[Sequence[str]],
    *,
    cwd: Path | str | None = None,
    timeout_seconds: int = 120,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run explicit local validation commands and return results plus errors.

    Commands are executed without a shell. This helper is intentionally generic:
    policy about which commands are safe belongs in the graph state and operator
    workflow, not in hidden defaults.
    """
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for command in commands:
        argv = list(command)
        if not argv:
            raise ValueError("gate command cannot be empty")

        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
            )
            result = {
                "command": argv,
                "returncode": completed.returncode,
                "stdout": _clip(completed.stdout),
                "stderr": _clip(completed.stderr),
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": argv,
                "returncode": 124,
                "stdout": _clip(_as_text(exc.stdout)),
                "stderr": _clip(_as_text(exc.stderr) or f"timed out after {timeout_seconds}s"),
            }

        results.append(result)
        if result["returncode"] != 0:
            errors.append(
                {
                    "node": "gate_execution",
                    "domain": "ci",
                    "message": f"command failed: {' '.join(argv)}",
                    "returncode": result["returncode"],
                    "stderr": result["stderr"],
                }
            )

    return results, errors


def select_gate_commands_for_mutations(paths: Iterable[str]) -> list[list[str]]:
    """Select local, workspace-safe gates from proposed mutation paths."""
    normalized = [path.split(":", 1)[1] if ":" in path else path for path in paths]
    if not normalized:
        return []
    if any(path.endswith(".py") for path in normalized):
        return [[sys.executable, "-m", "compileall", "-q", "."]]
    if all(path.startswith("docs/") or path.endswith((".md", ".txt", ".rst")) for path in normalized):
        return [
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "[p.read_text(encoding='utf-8') for p in Path('.').rglob('*') if p.is_file()]"
                ),
            ]
        ]
    return [[sys.executable, "-c", "from pathlib import Path; assert any(Path('.').rglob('*'))"]]
