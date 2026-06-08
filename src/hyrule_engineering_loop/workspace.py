"""Safe temporary workspace mutation helpers."""

from __future__ import annotations

import shutil
import tempfile
import os
from pathlib import Path

DEFAULT_WORKSPACE_PREFIX = "hyrule-engineering-loop-"


def _safe_relative_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe mutation path: {path}")
    return candidate


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


def write_mutations_to_workspace(mutations: dict[str, str]) -> tuple[Path, list[str]]:
    """Write proposed file-content mutations into an isolated temp workspace."""
    parent = configured_workspace_parent()
    root = Path(tempfile.mkdtemp(prefix=DEFAULT_WORKSPACE_PREFIX, dir=parent))
    written: list[str] = []
    try:
        for raw_path, content in mutations.items():
            relative = _safe_relative_path(raw_path)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(str(relative))
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
