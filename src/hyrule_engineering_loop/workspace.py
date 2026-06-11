"""Safe temporary workspace mutation helpers."""

from __future__ import annotations

import shutil
import tempfile
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE_PREFIX = "hyrule-engineering-loop-"


@dataclass(frozen=True)
class WorkspaceMutation:
    """Normalized file mutation for temporary workspace application."""

    path: Path
    content: str
    operation: str


def _safe_relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe mutation path: {path}")
    return candidate


def normalize_mutation_operations(
    mutations: dict[str, str],
    operations: list[dict[str, Any]] | None = None,
) -> list[WorkspaceMutation]:
    """Normalize legacy path/content maps plus optional operation metadata."""
    operation_by_path: dict[str, dict[str, Any]] = {}
    for operation_metadata in operations or []:
        raw_path = operation_metadata.get("path")
        if isinstance(raw_path, str):
            operation_by_path[raw_path] = operation_metadata

    normalized: list[WorkspaceMutation] = []
    seen: set[str] = set()
    for raw_path, content in mutations.items():
        metadata = operation_by_path.get(raw_path, {})
        operation_name = str(metadata.get("operation", "create"))
        normalized.append(
            WorkspaceMutation(
                path=_safe_relative_path(raw_path),
                content=str(metadata.get("content", content)),
                operation=operation_name,
            )
        )
        seen.add(raw_path)

    for raw_path, metadata in operation_by_path.items():
        if raw_path in seen:
            continue
        normalized.append(
            WorkspaceMutation(
                path=_safe_relative_path(raw_path),
                content=str(metadata.get("content", "")),
                operation=str(metadata.get("operation", "create")),
            )
        )

    return normalized


def configured_workspace_parent() -> Path | None:
    """Return the configured workspace parent, creating it if needed."""
    raw_root = os.environ.get("HYRULE_WORKSPACE_ROOT")
    if not raw_root:
        return None

    parent = Path(raw_root).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        raise ValueError(f"HYRULE_WORKSPACE_ROOT is not a directory: {parent}")
    return parent


def write_mutations_to_workspace(
    mutations: dict[str, str],
    operations: list[dict[str, Any]] | None = None,
) -> tuple[Path, list[str]]:
    """Write proposed file-content mutations into an isolated temp workspace."""
    parent = configured_workspace_parent()
    root = Path(tempfile.mkdtemp(prefix=DEFAULT_WORKSPACE_PREFIX, dir=parent))
    written: list[str] = []
    try:
        for mutation in normalize_mutation_operations(mutations, operations):
            if mutation.operation not in {"create", "replace"}:
                raise ValueError(f"unsupported mutation operation: {mutation.operation}")
            target = root / mutation.path
            if mutation.operation == "create" and target.exists():
                raise ValueError(f"create mutation target already exists: {mutation.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(mutation.content, encoding="utf-8")
            written.append(str(mutation.path))
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root, written


def cleanup_workspace(path: str | None) -> bool:
    """Remove a temporary workspace if present."""
    if not path:
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not Path(path).exists()
