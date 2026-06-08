"""Repository discovery and safety checks for engineering-loop dry runs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.state import GraphState


class RepoAdapterError(RuntimeError):
    """Raised when a target repository is not safe for mutation staging."""


@dataclass(frozen=True)
class RepoAdapterResult:
    """Normalized repository metadata for promotion."""

    name: str
    path: Path
    branch: str
    base_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "branch": self.branch,
            "base_ref": self.base_ref,
        }


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise RepoAdapterError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def default_workspace_root() -> Path:
    """Return the parent directory that usually contains sibling hyrule repos."""
    return Path.cwd().resolve().parent


def discover_hyrule_repositories(workspace_root: str | Path | None = None) -> dict[str, Path]:
    """Discover sibling ``hyrule-*`` git checkouts."""
    root = Path(workspace_root).expanduser().resolve() if workspace_root else default_workspace_root()
    repos: dict[str, Path] = {}
    if not root.exists():
        raise RepoAdapterError(f"repo workspace root does not exist: {root}")

    for candidate in sorted(root.glob("hyrule-*")):
        if candidate.is_dir() and (candidate / ".git").exists():
            repos[candidate.name] = candidate.resolve()
    return repos


def _current_branch(repo_path: Path) -> str:
    try:
        return _run_git(["symbolic-ref", "--short", "HEAD"], cwd=repo_path).stdout.strip()
    except RepoAdapterError as exc:
        raise RepoAdapterError(f"repo is detached: {repo_path}") from exc


def _ensure_clean(repo_path: Path) -> None:
    status = _run_git(["status", "--porcelain"], cwd=repo_path).stdout.strip()
    if status:
        raise RepoAdapterError(f"repo has uncommitted changes: {repo_path}")


def verify_repository(repo_path: str | Path, *, base_ref: str = "HEAD") -> RepoAdapterResult:
    """Verify a repo is clean, attached, and has a valid base ref."""
    path = Path(repo_path).expanduser().resolve()
    if not (path / ".git").exists():
        raise RepoAdapterError(f"not a git checkout: {path}")
    branch = _current_branch(path)
    _ensure_clean(path)
    _run_git(["rev-parse", "--verify", base_ref], cwd=path)
    return RepoAdapterResult(name=path.name, path=path, branch=branch, base_ref=base_ref)


def resolve_repositories_for_state(state: GraphState) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Resolve and verify promotion repositories requested by graph state."""
    base_ref = state.get("promotion_base_ref", "HEAD")
    discovered = discover_hyrule_repositories(state.get("repo_workspace_root"))
    requested = state.get("promotion_repo_names")

    if requested:
        repo_names = requested
    elif state.get("promotion_repositories"):
        repo_names = list(state.get("promotion_repositories", {}))
    else:
        repo_names = []

    resolved: dict[str, str] = dict(state.get("promotion_repositories", {}))
    results: list[dict[str, Any]] = []
    for name in repo_names:
        raw_repo_path = resolved.get(name) or discovered.get(name)
        if raw_repo_path is None:
            raise RepoAdapterError(f"unknown repo: {name}")
        repo_path = Path(raw_repo_path)
        verified = verify_repository(repo_path, base_ref=base_ref)
        resolved[name] = str(verified.path)
        result = verified.as_dict()
        result["name"] = name
        results.append(result)

    return resolved, results
