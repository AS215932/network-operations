"""Branch-backed worktree promotion for validated mutations."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.state import GraphState


class PromotionError(RuntimeError):
    """Raised when a mutation cannot be safely promoted."""


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


def diff_preview_from_results(results: list[dict[str, Any]], *, max_chars: int = 4_000) -> list[dict[str, Any]]:
    """Return compact diff previews suitable for CLI/Pi summaries."""
    previews: list[dict[str, Any]] = []
    for result in results:
        diff = str(result.get("diff", ""))
        previews.append(
            {
                "repo": result.get("repo"),
                "branch": result.get("branch"),
                "written_files": result.get("written_files", []),
                "diff_excerpt": diff[:max_chars],
                "diff_truncated": len(diff) > max_chars,
            }
        )
    return previews


def setup_worktrees_for_state(state: GraphState) -> list[dict[str, Any]]:
    """Create branch-backed worktrees *before* implementation (v2 Phase B).

    Idempotent: when ``worktree_results`` already records live worktrees for
    this change (a remediation round), they are returned unchanged so the
    backend keeps iterating in the same tree.
    """
    if not state.get("promotion_enabled", False):
        return []
    repo_roots = state.get("promotion_repositories", {})
    if not repo_roots:
        return []

    existing = state.get("worktree_results") or []
    if existing and all(Path(str(item.get("worktree_path", ""))).is_dir() for item in existing):
        return list(existing)

    worktree_parent = Path(
        state.get("promotion_worktree_root")
        or tempfile.mkdtemp(prefix="hyrule-engineering-promotion-root-")
    ).resolve()
    worktree_parent.mkdir(parents=True, exist_ok=True)
    branch_prefix = state.get("promotion_branch_prefix", "hyrule-loop")
    base_ref = state.get("promotion_base_ref", "HEAD")

    results: list[dict[str, Any]] = []
    created: list[tuple[Path, str, Path]] = []
    try:
        for repo_name in sorted(repo_roots):
            repo_path = Path(repo_roots[repo_name]).expanduser().resolve()
            if not (repo_path / ".git").exists():
                raise PromotionError(f"repo path is not a git checkout: {repo_path}")

            branch = f"{branch_prefix}/{_slug(state['change_id'])}/{_slug(repo_name)}"
            worktree_path = worktree_parent / f"{_slug(repo_name)}-{_slug(state['change_id'])}"
            if worktree_path.exists():
                raise PromotionError(f"worktree path already exists: {worktree_path}")
            _run_git(["worktree", "add", "-b", branch, str(worktree_path), base_ref], cwd=repo_path)
            created.append((repo_path, branch, worktree_path))
            results.append(
                {
                    "repo": repo_name,
                    "repo_path": str(repo_path),
                    "branch": branch,
                    "worktree_path": str(worktree_path),
                    "base_ref": base_ref,
                }
            )
    except Exception:
        for repo_path, branch, worktree_path in reversed(created):
            _cleanup_worktree(repo_path, branch, worktree_path)
        raise

    return results


def _status_codes(worktree_path: Path) -> list[tuple[str, str]]:
    raw = _run_git(["status", "--porcelain"], cwd=worktree_path).stdout
    entries: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append((code, path.strip().strip('"')))
    return entries


def _operation_for_code(code: str) -> str:
    if "D" in code:
        return "delete"
    if "M" in code or "R" in code:
        return "replace"
    return "create"


def capture_worktree_results(state: GraphState) -> list[dict[str, Any]]:
    """Capture diffs from backend-mutated worktrees into promotion results.

    The result shape is byte-compatible with the v1 ``promote_mutations``
    output so the PR boundary, trace summarizers, and operator cleanup
    commands keep working unchanged.
    """
    operation_by_key: dict[str, str] = {}
    for metadata in state.get("proposed_mutation_operations", []):
        raw_path = metadata.get("path")
        if isinstance(raw_path, str):
            operation_by_key[raw_path] = str(metadata.get("operation", "create"))

    results: list[dict[str, Any]] = []
    for worktree in state.get("worktree_results") or []:
        repo_name = str(worktree.get("repo", ""))
        worktree_path = Path(str(worktree.get("worktree_path", "")))
        if not worktree_path.is_dir():
            raise PromotionError(f"promoted worktree is missing: {worktree_path}")

        _run_git(["add", "-A", "-N", "--", "."], cwd=worktree_path)
        diff = _run_git(["diff", "--", "."], cwd=worktree_path).stdout
        entries = _status_codes(worktree_path)
        written_files = sorted({path for _, path in entries})
        mutation_operations = [
            {
                "path": f"{repo_name}:{path}",
                "operation": operation_by_key.get(f"{repo_name}:{path}", _operation_for_code(code)),
            }
            for code, path in entries
        ]
        results.append(
            {
                "repo": repo_name,
                "repo_path": str(worktree.get("repo_path", "")),
                "branch": str(worktree.get("branch", "")),
                "worktree_path": str(worktree_path),
                "written_files": written_files,
                "mutation_operations": mutation_operations,
                "diff": diff,
                "requires_human_signoff": True,
            }
        )
    return results


def rollback_promotions(results: list[dict[str, Any]]) -> None:
    """Remove promoted worktrees and branches from prior promotion results."""
    for result in reversed(results):
        _cleanup_worktree(
            Path(str(result["repo_path"])),
            str(result["branch"]),
            Path(str(result["worktree_path"])),
        )
