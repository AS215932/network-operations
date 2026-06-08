"""Branch-backed worktree promotion for validated mutations."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.state import GraphState
from hyrule_engineering_loop.workspace import _safe_relative_path


class PromotionError(RuntimeError):
    """Raised when a mutation cannot be safely promoted."""


@dataclass(frozen=True)
class ParsedMutation:
    repo: str
    path: Path
    content: str


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise PromotionError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]+", "-", value).strip("-") or "change"


def _parse_mutations(mutations: dict[str, str]) -> list[ParsedMutation]:
    parsed: list[ParsedMutation] = []
    for key, content in mutations.items():
        if ":" not in key:
            continue
        repo, raw_path = key.split(":", 1)
        if not repo:
            raise PromotionError(f"empty repo in mutation key: {key}")
        parsed.append(ParsedMutation(repo=repo, path=_safe_relative_path(raw_path), content=content))
    return parsed


def _path_allowed(path: Path, allowed_prefixes: list[str]) -> bool:
    if not allowed_prefixes:
        return False
    for raw_prefix in allowed_prefixes:
        prefix = _safe_relative_path(raw_prefix)
        if path == prefix or path.is_relative_to(prefix):
            return True
    return False


def _cleanup_worktree(repo_path: Path, branch: str, worktree_path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_path,
        capture_output=True,
        check=False,
        text=True,
    )
    subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=repo_path,
        capture_output=True,
        check=False,
        text=True,
    )


def promote_mutations(state: GraphState) -> list[dict[str, Any]]:
    """Promote explicit ``repo:path`` mutations into branch-backed worktrees."""
    parsed = _parse_mutations(state["proposed_mutations"])
    if not parsed:
        return []

    repo_roots = state.get("promotion_repositories", {})
    allowed_paths = state.get("promotion_allowed_paths", {})
    worktree_parent = Path(
        state.get("promotion_worktree_root")
        or tempfile.mkdtemp(prefix="hyrule-engineering-promotion-root-")
    ).resolve()
    worktree_parent.mkdir(parents=True, exist_ok=True)
    branch_prefix = state.get("promotion_branch_prefix", "hyrule-loop")

    results: list[dict[str, Any]] = []
    created: list[tuple[Path, str, Path]] = []

    try:
        for mutation in parsed:
            if mutation.repo not in repo_roots:
                raise PromotionError(f"repo not allowlisted for promotion: {mutation.repo}")
            if not _path_allowed(mutation.path, allowed_paths.get(mutation.repo, [])):
                raise PromotionError(
                    f"path not allowlisted for {mutation.repo}: {mutation.path.as_posix()}"
                )

            repo_path = Path(repo_roots[mutation.repo]).expanduser().resolve()
            if not (repo_path / ".git").exists():
                raise PromotionError(f"repo path is not a git checkout: {repo_path}")

            branch = f"{branch_prefix}/{_slug(state['change_id'])}/{_slug(mutation.repo)}"
            worktree_path = worktree_parent / f"{_slug(mutation.repo)}-{_slug(state['change_id'])}"
            _run_git(["worktree", "add", "-b", branch, str(worktree_path), "HEAD"], cwd=repo_path)
            created.append((repo_path, branch, worktree_path))

            target = worktree_path / mutation.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(mutation.content, encoding="utf-8")
            _run_git(["add", "-N", "."], cwd=worktree_path)
            diff = _run_git(["diff", "--", "."], cwd=worktree_path).stdout

            results.append(
                {
                    "repo": mutation.repo,
                    "repo_path": str(repo_path),
                    "branch": branch,
                    "worktree_path": str(worktree_path),
                    "written_files": [mutation.path.as_posix()],
                    "diff": diff,
                    "requires_human_signoff": True,
                }
            )
    except Exception:
        for repo_path, branch, worktree_path in reversed(created):
            _cleanup_worktree(repo_path, branch, worktree_path)
        raise

    if not results:
        shutil.rmtree(worktree_parent, ignore_errors=True)
    return results


def rollback_promotions(results: list[dict[str, Any]]) -> None:
    """Remove promoted worktrees and branches from prior promotion results."""
    for result in reversed(results):
        _cleanup_worktree(
            Path(str(result["repo_path"])),
            str(result["branch"]),
            Path(str(result["worktree_path"])),
        )
