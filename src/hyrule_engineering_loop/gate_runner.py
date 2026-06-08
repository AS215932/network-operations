"""Local command gate execution for the engineering loop."""

from __future__ import annotations

import subprocess
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
