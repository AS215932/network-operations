"""Repository discovery and safety checks for engineering-loop dry runs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.state import GraphState
from hyrule_engineering_loop.workspace import _safe_relative_path


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


def _read_context_file(repo_path: Path, relative_path: str, *, max_bytes: int) -> dict[str, Any]:
    path = _safe_relative_path(relative_path)
    target = repo_path / path
    if not target.exists() or not target.is_file():
        return {"path": path.as_posix(), "status": "missing"}
    content = target.read_text(encoding="utf-8", errors="replace")
    clipped = content[:max_bytes]
    return {
        "path": path.as_posix(),
        "status": "read",
        "chars": len(content),
        "content": clipped,
        "truncated": len(content) > len(clipped),
    }


def build_repo_context_bundle(
    state: GraphState,
    *,
    max_files_per_repo: int = 20,
    max_file_bytes: int = 12_000,
) -> dict[str, Any]:
    """Build compact target-repo context for implementation writer nodes."""
    repo_roots = state.get("promotion_repositories", {})
    allowed_paths = state.get("promotion_allowed_paths", {})
    source_by_repo: dict[str, list[str]] = {}
    for raw_source in state["source_of_truth_files"]:
        if ":" not in raw_source:
            continue
        repo_name, path = raw_source.split(":", 1)
        source_by_repo.setdefault(repo_name, []).append(path)

    repos: list[dict[str, Any]] = []
    for repo_name, raw_path in sorted(repo_roots.items()):
        repo_path = Path(raw_path).expanduser().resolve()
        files: list[dict[str, Any]] = []
        for source_path in source_by_repo.get(repo_name, ["README.md"])[:max_files_per_repo]:
            files.append(_read_context_file(repo_path, source_path, max_bytes=max_file_bytes))
        repos.append(
            {
                "name": repo_name,
                "path": str(repo_path),
                "allowed_paths": allowed_paths.get(repo_name, []),
                "source_files": files,
            }
        )

    return {
        "repos": repos,
        "feature_target_repo": state.get("feature_target_repo"),
        "feature_plan_path": state.get("feature_plan_path"),
    }
