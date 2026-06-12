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
    operation: str


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


def _parse_mutations(state: GraphState) -> list[ParsedMutation]:
    mutations = state["proposed_mutations"]
    operation_by_path: dict[str, dict[str, Any]] = {}
    for operation in state.get("proposed_mutation_operations", []):
        raw_path = operation.get("path")
        if isinstance(raw_path, str):
            operation_by_path[raw_path] = operation

    parsed: list[ParsedMutation] = []
    for key, content in mutations.items():
        if ":" not in key:
            continue
        repo, raw_path = key.split(":", 1)
        if not repo:
            raise PromotionError(f"empty repo in mutation key: {key}")
        metadata = operation_by_path.get(key, {})
        parsed.append(
            ParsedMutation(
                repo=repo,
                path=_safe_relative_path(raw_path),
                content=str(metadata.get("content", content)),
                operation=str(metadata.get("operation", "create")),
            )
        )

    parsed_keys = {f"{mutation.repo}:{mutation.path.as_posix()}" for mutation in parsed}
    for key, metadata in operation_by_path.items():
        if key in parsed_keys or ":" not in key:
            continue
        repo, raw_path = key.split(":", 1)
        parsed.append(
            ParsedMutation(
                repo=repo,
                path=_safe_relative_path(raw_path),
                content=str(metadata.get("content", "")),
                operation=str(metadata.get("operation", "create")),
            )
        )
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


def promote_mutations(state: GraphState) -> list[dict[str, Any]]:
    """Promote explicit ``repo:path`` mutations into branch-backed worktrees."""
    parsed = _parse_mutations(state)
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
    by_repo: dict[str, list[ParsedMutation]] = {}
    for mutation in parsed:
        by_repo.setdefault(mutation.repo, []).append(mutation)

    try:
        for repo_name, repo_mutations in by_repo.items():
            if repo_name not in repo_roots:
                raise PromotionError(f"repo not allowlisted for promotion: {repo_name}")
            for mutation in repo_mutations:
                if mutation.operation not in {"create", "replace"}:
                    raise PromotionError(f"unsupported mutation operation: {mutation.operation}")
                if not _path_allowed(mutation.path, allowed_paths.get(repo_name, [])):
                    raise PromotionError(
                        f"path not allowlisted for {repo_name}: {mutation.path.as_posix()}"
                    )

            repo_path = Path(repo_roots[repo_name]).expanduser().resolve()
            if not (repo_path / ".git").exists():
                raise PromotionError(f"repo path is not a git checkout: {repo_path}")

            branch = f"{branch_prefix}/{_slug(state['change_id'])}/{_slug(repo_name)}"
            worktree_path = worktree_parent / f"{_slug(repo_name)}-{_slug(state['change_id'])}"
            _run_git(["worktree", "add", "-b", branch, str(worktree_path), "HEAD"], cwd=repo_path)
            created.append((repo_path, branch, worktree_path))

            written_files: list[str] = []
            mutation_operations: list[dict[str, str]] = []
            for mutation in repo_mutations:
                target = worktree_path / mutation.path
                if mutation.operation == "create" and target.exists():
                    raise PromotionError(
                        f"create mutation target already exists: {repo_name}:{mutation.path.as_posix()}"
                    )
                if mutation.operation == "replace" and not target.exists():
                    raise PromotionError(
                        f"replace mutation target does not exist: {repo_name}:{mutation.path.as_posix()}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(mutation.content, encoding="utf-8")
                written_files.append(mutation.path.as_posix())
                mutation_operations.append(
                    {
                        "path": f"{repo_name}:{mutation.path.as_posix()}",
                        "operation": mutation.operation,
                    }
                )
            _run_git(["add", "-N", "."], cwd=worktree_path)
            diff = _run_git(["diff", "--", "."], cwd=worktree_path).stdout

            results.append(
                {
                    "repo": repo_name,
                    "repo_path": str(repo_path),
                    "branch": branch,
                    "worktree_path": str(worktree_path),
                    "written_files": written_files,
                    "mutation_operations": mutation_operations,
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
