"""Coding-agent backend abstraction for the Hyrule Engineering Loop.

Phase B of the v2 architecture (``docs/engineering-loop/v2-architecture.md``
§1): generation moves out of one-shot structured completions and into an
``AgentBackend`` that executes inside an already-created branch-backed
worktree. ``MockBackend`` absorbs the v1 whole-file ``create``/``replace``
mutation semantics so the loop stays fully testable offline; ``PiBackend``
and ``ClaudeCodeBackend`` drive real coding-agent harnesses as scrubbed
subprocesses.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, TypeAlias

import yaml

from hyrule_engineering_loop.state import GraphState
from hyrule_engineering_loop.workspace import (
    _safe_relative_path,
    write_mutations_to_workspace,
)

GateResult: TypeAlias = dict[str, Any]
BackendStatus = Literal["completed", "budget_exhausted", "failed"]

DEFAULT_MAX_ITERATIONS = 12
DEFAULT_MAX_WALL_CLOCK_SECONDS = 1800.0

KNOWN_BACKENDS = ("mock", "pi", "claude-code")

# Environment hygiene: the backend gets the repo and its toolchain, nothing
# else. Allowlist, not denylist — anything not named here never reaches the
# backend process (no Vault, no fleet SSH agent, no provider API keys).
ENV_ALLOWED_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "TERM",
        "TMPDIR",
        "TZ",
        "COLUMNS",
        "LINES",
        "PYTHONUNBUFFERED",
    }
)
ENV_ALLOWED_PREFIXES = ("LC_",)
ENV_DENIED_PATTERN = re.compile(
    r"(?i)(token|secret|passwd|password|api[_-]?key|vault|ssh|aws_|credential)"
)


class BackendExecutionError(RuntimeError):
    """Raised when a backend cannot execute at all (configuration errors)."""


@dataclass(frozen=True)
class TaskSpec:
    """The task contract handed to a backend.

    Phase C populates this from the parsed ``tasks/<change-id>.md`` sprint
    contract when one is present in graph state; the acceptance criteria,
    non-goals, role consult constraints, and any judgment findings from the
    previous round all reach the backend prompt.
    """

    change_id: str
    change_class: str
    risk_level: str
    request: str
    allowed_paths: Mapping[str, tuple[str, ...]]
    gate_commands: tuple[tuple[str, ...], ...] = ()
    transcript_dir: str | None = None
    intent: str = ""
    acceptance_criteria: tuple[str, ...] = ()
    non_goals: str = ""
    role_constraints: tuple[str, ...] = ()
    remediation_findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CostReport:
    """Tokens/dollars where the harness reports them; never silently absent."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    usd: float | None = None
    reported: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": self.usd,
            "reported": self.reported,
        }


@dataclass(frozen=True)
class BackendConstraints:
    """Hard limits and scope for one backend execution."""

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_wall_clock_seconds: float = DEFAULT_MAX_WALL_CLOCK_SECONDS
    max_cost_usd: float | None = None
    network_scope: Literal["none", "package_registries", "full"] = "none"
    read_only: bool = False


@dataclass(frozen=True)
class AgentRunResult:
    """Outcome of one backend execution inside a worktree."""

    status: BackendStatus
    diff: str
    changed_paths: tuple[str, ...]
    transcript_path: str | None
    gate_evidence: tuple[GateResult, ...]
    iterations: int
    wall_clock_seconds: float
    cost: CostReport
    backend: str
    notes: str = ""
    error: str | None = None
    workspace_root: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Trace-safe summary: paths and counters, never the diff body."""
        return {
            "status": self.status,
            "backend": self.backend,
            "changed_paths": list(self.changed_paths),
            "diff_chars": len(self.diff),
            "transcript_path": self.transcript_path,
            "iterations": self.iterations,
            "wall_clock_seconds": round(self.wall_clock_seconds, 3),
            "cost": self.cost.as_dict(),
            "notes": self.notes,
            "error": self.error,
        }


class AgentBackend(Protocol):
    """Executor protocol: run one tranche inside a guarded worktree."""

    name: str

    def execute(
        self,
        *,
        task_spec: TaskSpec,
        worktree: Path | None,
        constraints: BackendConstraints,
    ) -> AgentRunResult:
        """Execute the task; ``worktree=None`` is a MockBackend-only scratch mode."""
        ...


def scrubbed_backend_env(*, allow_names: frozenset[str] | set[str] | None = None) -> dict[str, str]:
    """Build the allowlisted environment a backend subprocess receives."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in ENV_ALLOWED_NAMES or key.startswith(ENV_ALLOWED_PREFIXES):
            env[key] = value
        elif allow_names and key in allow_names:
            env[key] = value
    return env


def env_hygiene_violations(env: Mapping[str, str]) -> list[str]:
    """Return env var names that look credential-bearing (defense in depth)."""
    return sorted(key for key in env if ENV_DENIED_PATTERN.search(key))


def loop_repo_root() -> Path:
    """Return the engineering-loop repo root (where ``skills/`` lives)."""
    return Path(__file__).resolve().parents[2]


def load_skills_index(root: Path | None = None) -> list[dict[str, str]]:
    """Parse ``skills/*/SKILL.md`` frontmatter into a compact injectable index."""
    skills_dir = (root or loop_repo_root()) / "skills"
    index: list[dict[str, str]] = []
    if not skills_dir.is_dir():
        return index
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end < 0:
            continue
        try:
            frontmatter = yaml.safe_load(text[3:end])
        except yaml.YAMLError:
            continue
        if not isinstance(frontmatter, dict):
            continue
        index.append(
            {
                "name": str(frontmatter.get("name", skill_file.parent.name)),
                "description": str(frontmatter.get("description", "")),
                "path": str(skill_file.relative_to(root or loop_repo_root())),
            }
        )
    return index


def load_lessons(repo: str | None, root: Path | None = None) -> str | None:
    """Return ``memory/lessons/<repo>.md`` when present (Phase D populates it)."""
    if not repo:
        return None
    lessons_path = (root or loop_repo_root()) / "memory" / "lessons" / f"{repo}.md"
    if lessons_path.is_file():
        return lessons_path.read_text(encoding="utf-8")
    return None


def assemble_backend_prompt(task_spec: TaskSpec, constraints: BackendConstraints) -> str:
    """Compose the harness-agnostic prompt: spec, boundaries, skills, lessons."""
    lines: list[str] = [
        f"# Engineering loop tranche: {task_spec.change_id}",
        "",
        f"Change class: {task_spec.change_class}; risk: {task_spec.risk_level}.",
        "",
        "## Request",
        "",
        task_spec.intent.rstrip() or task_spec.request.rstrip() or "(no request text supplied)",
    ]
    if task_spec.acceptance_criteria:
        lines.extend(["", "## Acceptance criteria (the definition of done)", ""])
        lines.extend(
            f"{index}. {criterion}"
            for index, criterion in enumerate(task_spec.acceptance_criteria, start=1)
        )
    if task_spec.non_goals:
        lines.extend(["", "## Non-goals", "", task_spec.non_goals.rstrip()])
    if task_spec.role_constraints:
        lines.extend(["", "## Role consult constraints", ""])
        lines.extend(f"- {constraint}" for constraint in task_spec.role_constraints)
    if task_spec.remediation_findings:
        lines.extend(["", "## Findings to address (previous judgment round)", ""])
        lines.extend(f"- {finding}" for finding in task_spec.remediation_findings)
    lines.extend([
        "",
        "## Boundaries",
        "",
        "- Work only inside this worktree; do not commit, push, or open PRs.",
        "- Touch only paths under the allowed prefixes below; anything else fails policy.",
        "- No secret material, credentials, or environment-specific tokens in any file.",
        f"- Budget: {constraints.max_iterations} iterations, "
        f"{int(constraints.max_wall_clock_seconds)}s wall clock.",
    ])
    for repo, prefixes in sorted(task_spec.allowed_paths.items()):
        lines.append(f"- Allowed paths ({repo}): {', '.join(prefixes) or 'none configured'}")
    if task_spec.gate_commands:
        lines.extend(["", "## Gates to keep green", ""])
        lines.extend(f"- {' '.join(command)}" for command in task_spec.gate_commands)
    skills = load_skills_index()
    if skills:
        lines.extend(["", "## Skills (read the full file on demand)", ""])
        lines.extend(f"- {item['name']}: {item['description']}" for item in skills)
    for repo in sorted(task_spec.allowed_paths):
        lessons = load_lessons(repo)
        if lessons:
            lines.extend(["", f"## Lessons for {repo}", "", lessons.rstrip()])
    lines.append("")
    return "\n".join(lines)


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise BackendExecutionError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def _status_paths(worktree: Path) -> list[str]:
    raw = _run_git(["status", "--porcelain"], cwd=worktree).stdout
    paths: list[str] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return sorted(set(paths))


def capture_worktree_diff(worktree: Path) -> tuple[str, list[str]]:
    """Stage intent-to-add for untracked files and capture the worktree diff."""
    _run_git(["add", "-A", "-N", "--", "."], cwd=worktree)
    diff = _run_git(["diff", "--", "."], cwd=worktree).stdout
    return diff, _status_paths(worktree)


def reset_worktree(worktree: Path) -> None:
    """Discard uncommitted backend output so remediation retries stay idempotent."""
    subprocess.run(
        ["git", "reset", "--quiet", "--", "."],
        cwd=worktree,
        capture_output=True,
        check=False,
        text=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "--", "."],
        cwd=worktree,
        capture_output=True,
        check=False,
        text=True,
    )
    subprocess.run(
        ["git", "clean", "--quiet", "-fd", "--", "."],
        cwd=worktree,
        capture_output=True,
        check=False,
        text=True,
    )


@dataclass(frozen=True)
class MutationOperation:
    """One v1-style whole-file mutation, already repo-scoped."""

    path: Path
    content: str
    operation: str


class MockBackend:
    """Deterministic backend absorbing the v1 writer mutation semantics.

    It applies pre-resolved whole-file ``create``/``replace`` mutations either
    into the branch-backed worktree (promotion runs) or into an isolated temp
    scratch workspace (legacy non-promotion runs — the only place the v1
    temp-workspace path survives). It never calls a provider itself; mutation
    resolution stays in the delegation node, exactly as model-routed in v1.
    """

    name = "mock"

    def __init__(
        self,
        *,
        mutations: Mapping[str, str],
        operations: list[dict[str, Any]] | None = None,
        repo: str | None = None,
    ) -> None:
        self._mutations = dict(mutations)
        self._operations = list(operations or [])
        self._repo = repo

    def _repo_mutations(self) -> list[MutationOperation]:
        operation_by_path: dict[str, dict[str, Any]] = {}
        for metadata in self._operations:
            raw_path = metadata.get("path")
            if isinstance(raw_path, str):
                operation_by_path[raw_path] = metadata

        resolved: list[MutationOperation] = []
        keys = set(self._mutations) | set(operation_by_path)
        for key in sorted(keys):
            repo: str | None = None
            raw_path = key
            if ":" in key:
                repo, raw_path = key.split(":", 1)
            if self._repo is not None and repo != self._repo:
                continue
            if self._repo is None and repo is not None:
                continue
            metadata = operation_by_path.get(key, {})
            content = str(metadata.get("content", self._mutations.get(key, "")))
            resolved.append(
                MutationOperation(
                    path=_safe_relative_path(raw_path),
                    content=content,
                    operation=str(metadata.get("operation", "create")),
                )
            )
        return resolved

    def _apply_to_worktree(self, worktree: Path) -> list[str]:
        written: list[str] = []
        for mutation in self._repo_mutations():
            if mutation.operation not in {"create", "replace"}:
                raise BackendExecutionError(
                    f"unsupported mutation operation: {mutation.operation}"
                )
            target = worktree / mutation.path
            if mutation.operation == "create" and target.exists():
                if (
                    target.is_file()
                    and target.read_text(encoding="utf-8") == mutation.content
                ):
                    # Idempotent re-apply across remediation rounds.
                    written.append(mutation.path.as_posix())
                    continue
                raise BackendExecutionError(
                    f"create mutation target already exists: "
                    f"{self._repo}:{mutation.path.as_posix()}"
                )
            if mutation.operation == "replace" and not target.exists():
                raise BackendExecutionError(
                    f"replace mutation target does not exist: "
                    f"{self._repo}:{mutation.path.as_posix()}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(mutation.content, encoding="utf-8")
            written.append(mutation.path.as_posix())
        return written

    def execute(
        self,
        *,
        task_spec: TaskSpec,
        worktree: Path | None,
        constraints: BackendConstraints,
    ) -> AgentRunResult:
        started = time.monotonic()
        if constraints.max_iterations < 1:
            return AgentRunResult(
                status="budget_exhausted",
                diff="",
                changed_paths=(),
                transcript_path=None,
                gate_evidence=(),
                iterations=0,
                wall_clock_seconds=time.monotonic() - started,
                cost=CostReport(reported=True, usd=0.0, input_tokens=0, output_tokens=0),
                backend=self.name,
                notes="iteration budget exhausted before execution",
            )

        if constraints.read_only:
            return AgentRunResult(
                status="completed",
                diff="",
                changed_paths=(),
                transcript_path=None,
                gate_evidence=(),
                iterations=1,
                wall_clock_seconds=time.monotonic() - started,
                cost=CostReport(reported=True, usd=0.0, input_tokens=0, output_tokens=0),
                backend=self.name,
                notes="deterministic read-only evaluation; no findings",
            )

        if worktree is not None:
            try:
                written = self._apply_to_worktree(worktree)
                diff, changed = capture_worktree_diff(worktree)
            except BackendExecutionError as exc:
                reset_worktree(worktree)
                return AgentRunResult(
                    status="failed",
                    diff="",
                    changed_paths=(),
                    transcript_path=None,
                    gate_evidence=(),
                    iterations=1,
                    wall_clock_seconds=time.monotonic() - started,
                    cost=CostReport(reported=True, usd=0.0, input_tokens=0, output_tokens=0),
                    backend=self.name,
                    error=str(exc),
                )
            return AgentRunResult(
                status="completed",
                diff=diff,
                changed_paths=tuple(changed or written),
                transcript_path=None,
                gate_evidence=(),
                iterations=1,
                wall_clock_seconds=time.monotonic() - started,
                cost=CostReport(reported=True, usd=0.0, input_tokens=0, output_tokens=0),
                backend=self.name,
                notes=f"applied {len(written)} mutation(s) to worktree",
            )

        # v1 parity: the temp-workspace writer applied every mutation key
        # verbatim (repo-prefixed keys become literal file names), and the
        # policy guard later judges the proposed mutations themselves.
        try:
            root, written = write_mutations_to_workspace(self._mutations, self._operations)
        except ValueError as exc:
            return AgentRunResult(
                status="failed",
                diff="",
                changed_paths=(),
                transcript_path=None,
                gate_evidence=(),
                iterations=1,
                wall_clock_seconds=time.monotonic() - started,
                cost=CostReport(reported=True, usd=0.0, input_tokens=0, output_tokens=0),
                backend=self.name,
                error=str(exc),
            )
        return AgentRunResult(
            status="completed",
            diff="",
            changed_paths=tuple(written),
            transcript_path=None,
            gate_evidence=(),
            iterations=1,
            wall_clock_seconds=time.monotonic() - started,
            cost=CostReport(reported=True, usd=0.0, input_tokens=0, output_tokens=0),
            backend=self.name,
            notes=f"applied {len(written)} mutation(s) to scratch workspace",
            workspace_root=str(root),
        )


class SubprocessBackend:
    """Shared driver for real coding-agent harnesses run as subprocesses."""

    name = "subprocess"
    default_command: tuple[str, ...] = ()
    extra_env_names: frozenset[str] = frozenset()

    def __init__(self, *, command: list[str] | None = None) -> None:
        self._command = list(command) if command else list(self.default_command)

    def build_command(self, *, prompt: str, constraints: BackendConstraints) -> list[str]:
        """Render the harness argv; ``{prompt}`` and ``{max_iterations}`` expand.

        In read-only evaluation mode the write-enabled permission mode is
        swapped for the harness's plan/read-only mode (the judgment-side
        write-guard still verifies the worktree afterwards).
        """
        rendered: list[str] = []
        for part in self._command:
            value = part.replace("{prompt}", prompt).replace(
                "{max_iterations}", str(constraints.max_iterations)
            )
            if constraints.read_only and value == "acceptEdits":
                value = "plan"
            rendered.append(value)
        return rendered

    def _parse_harness_output(self, stdout: str) -> dict[str, Any]:
        try:
            decoded = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def execute(
        self,
        *,
        task_spec: TaskSpec,
        worktree: Path | None,
        constraints: BackendConstraints,
    ) -> AgentRunResult:
        started = time.monotonic()

        def _result(
            status: BackendStatus,
            *,
            diff: str = "",
            changed: tuple[str, ...] = (),
            transcript: str | None = None,
            iterations: int = 0,
            cost: CostReport | None = None,
            notes: str = "",
            error: str | None = None,
        ) -> AgentRunResult:
            return AgentRunResult(
                status=status,
                diff=diff,
                changed_paths=changed,
                transcript_path=transcript,
                gate_evidence=(),
                iterations=iterations,
                wall_clock_seconds=time.monotonic() - started,
                cost=cost or CostReport(),
                backend=self.name,
                notes=notes,
                error=error,
            )

        if worktree is None:
            return _result("failed", error=f"{self.name} backend requires a branch-backed worktree")
        if constraints.max_iterations < 1:
            return _result("budget_exhausted", notes="iteration budget exhausted before execution")

        prompt = assemble_backend_prompt(task_spec, constraints)
        command = self.build_command(prompt=prompt, constraints=constraints)
        env = scrubbed_backend_env(allow_names=self.extra_env_names)
        leaked = env_hygiene_violations(env)
        if leaked:
            return _result("failed", error=f"backend env hygiene violation: {', '.join(leaked)}")

        try:
            completed = subprocess.run(
                command,
                cwd=worktree,
                env=env,
                capture_output=True,
                check=False,
                text=True,
                timeout=constraints.max_wall_clock_seconds,
            )
            stdout, stderr = completed.stdout, completed.stderr
            returncode: int | None = completed.returncode
            timed_out = False
        except FileNotFoundError as exc:
            return _result("failed", error=f"harness binary not found: {exc}")
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            returncode = None
            timed_out = True

        transcript_path: str | None = None
        if task_spec.transcript_dir:
            transcript_dir = Path(task_spec.transcript_dir).expanduser().resolve()
            transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript = transcript_dir / f"{task_spec.change_id}.{self.name}.transcript.txt"
            transcript.write_text(
                f"$ {' '.join(command)}\n\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
                encoding="utf-8",
            )
            transcript_path = str(transcript)

        parsed = self._parse_harness_output(stdout)
        iterations = int(parsed.get("num_turns", 1) or 1)
        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
        raw_cost = parsed.get("total_cost_usd")
        cost = CostReport(
            input_tokens=usage.get("input_tokens") if isinstance(usage, dict) else None,
            output_tokens=usage.get("output_tokens") if isinstance(usage, dict) else None,
            usd=float(raw_cost) if isinstance(raw_cost, (int, float)) else None,
            reported=isinstance(raw_cost, (int, float)),
        )

        try:
            diff, changed = capture_worktree_diff(worktree)
        except BackendExecutionError as exc:
            return _result(
                "failed",
                transcript=transcript_path,
                iterations=iterations,
                cost=cost,
                error=f"diff capture failed: {exc}",
            )

        if timed_out:
            return _result(
                "budget_exhausted",
                diff=diff,
                changed=tuple(changed),
                transcript=transcript_path,
                iterations=iterations,
                cost=cost,
                notes=f"wall clock budget exhausted after {int(constraints.max_wall_clock_seconds)}s; partial work kept for inspection",
            )
        if returncode != 0 or bool(parsed.get("is_error")):
            return _result(
                "failed",
                diff=diff,
                changed=tuple(changed),
                transcript=transcript_path,
                iterations=iterations,
                cost=cost,
                error=f"harness exited with code {returncode}",
            )
        return _result(
            "completed",
            diff=diff,
            changed=tuple(changed),
            transcript=transcript_path,
            iterations=iterations,
            cost=cost,
            notes=str(parsed.get("result", ""))[:400],
        )


class PiBackend(SubprocessBackend):
    """Non-interactive ``pi`` invocation in the worktree.

    The default argv mirrors the ``claude -p`` convention; override the
    command per-deployment through the ``backends.definitions`` section of
    ``model-policy.yml`` if the local ``pi`` build differs.
    """

    name = "pi"
    default_command = ("pi", "--print", "{prompt}")


class ClaudeCodeBackend(SubprocessBackend):
    """``claude -p`` headless invocation with pinned permission flags."""

    name = "claude-code"
    default_command = (
        "claude",
        "-p",
        "{prompt}",
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--max-turns",
        "{max_iterations}",
    )


def create_backend(
    name: str,
    *,
    command: list[str] | None = None,
    mutations: Mapping[str, str] | None = None,
    operations: list[dict[str, Any]] | None = None,
    repo: str | None = None,
) -> AgentBackend:
    """Instantiate a backend by policy name."""
    if name == "mock":
        return MockBackend(mutations=mutations or {}, operations=operations, repo=repo)
    if name == "pi":
        return PiBackend(command=command)
    if name == "claude-code":
        return ClaudeCodeBackend(command=command)
    raise BackendExecutionError(
        f"unknown backend: {name} (known: {', '.join(KNOWN_BACKENDS)})"
    )


def task_spec_from_state(state: GraphState) -> TaskSpec:
    """Build the backend task spec from graph state.

    When the planner has populated a parsed sprint contract in
    ``state["task_spec"]``, its repos/criteria/intent are authoritative;
    role consult constraints and the previous judgment round's findings are
    folded in so the backend sees the full, current contract.
    """
    spec = state.get("task_spec") or {}
    spec_repos = spec.get("repos") if isinstance(spec.get("repos"), dict) else {}
    if spec_repos:
        allowed = {str(repo): tuple(paths) for repo, paths in spec_repos.items()}
    else:
        allowed = {
            repo: tuple(paths)
            for repo, paths in state.get("promotion_allowed_paths", {}).items()
        }

    criteria: list[str] = [str(item) for item in spec.get("acceptance_criteria", [])]
    constraints_lines: list[str] = []
    for entry in state.get("role_constraints", []) or []:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", "unknown"))
        for constraint in entry.get("constraints", []):
            constraints_lines.append(f"{role}: {constraint}")
        for criterion in entry.get("acceptance_criteria", []):
            if str(criterion) not in criteria:
                criteria.append(str(criterion))

    findings: list[str] = []
    for finding in state.get("remediation_findings") or []:
        if not isinstance(finding, dict):
            continue
        location = str(finding.get("path") or "general")
        message = str(finding.get("message", ""))
        remediation = finding.get("suggested_remediation")
        line = f"{location}: {message}"
        if remediation:
            line += f" — {remediation}"
        findings.append(line)

    return TaskSpec(
        change_id=state["change_id"],
        change_class=str(state["change_class"]),
        risk_level=str(state["risk_level"]),
        request=state.get("feature_request", ""),
        allowed_paths=allowed,
        gate_commands=tuple(tuple(command) for command in state.get("gate_commands", [])),
        transcript_dir=state.get("handoff_output_dir") or os.environ.get("HYRULE_HANDOFF_DIR"),
        intent=str(spec.get("intent", "")),
        acceptance_criteria=tuple(criteria),
        non_goals=str(spec.get("non_goals", "")),
        role_constraints=tuple(constraints_lines),
        remediation_findings=tuple(findings),
    )


def constraints_from_state(state: GraphState) -> BackendConstraints:
    """Resolve backend budgets from graph state with safe defaults."""
    budget = state.get("backend_budget", {}) or {}
    max_iterations = budget.get("max_iterations", DEFAULT_MAX_ITERATIONS)
    raw_wall_clock = budget.get(
        "max_wall_clock_seconds",
        float(budget.get("max_wall_clock_minutes", 0)) * 60.0 or DEFAULT_MAX_WALL_CLOCK_SECONDS,
    )
    raw_cost = budget.get("max_cost_usd")
    return BackendConstraints(
        max_iterations=int(max_iterations),
        max_wall_clock_seconds=float(raw_wall_clock),
        max_cost_usd=float(raw_cost) if isinstance(raw_cost, (int, float)) else None,
        read_only=bool(budget.get("read_only", False)),
    )
