"""Task specs — the sprint contracts the loop builds and judges against.

Phase C of the v2 architecture (``docs/engineering-loop/v2-architecture.md``
§2): "done" is defined in ``tasks/<change-id>.md`` *before* generation
starts. The frontmatter is the machine-readable contract; the body carries
intent, testable acceptance criteria, non-goals, role consult notes, and a
rollback sketch. Evaluators grade the resulting diff against the criteria
recorded here. Format: ``docs/engineering-loop/templates/task-spec.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BODY_SECTIONS = (
    "Intent",
    "Acceptance criteria",
    "Done-conditions",
    "Non-goals",
    "Role consult notes",
    "Rollback sketch",
)

DEFAULT_BUDGET: dict[str, Any] = {
    "max_iterations": 20,
    "max_wall_clock_minutes": 45,
    "max_cost_usd": 5.0,
}


class TaskSpecError(RuntimeError):
    """Raised when a task spec is structurally invalid."""


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise TaskSpecError("task spec must start with a YAML frontmatter block")
    end = stripped.find("\n---", 3)
    if end < 0:
        raise TaskSpecError("task spec frontmatter is not terminated")
    raw = stripped[3:end]
    try:
        frontmatter = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TaskSpecError(f"task spec frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise TaskSpecError("task spec frontmatter must be a mapping")
    body = stripped[end + 4 :]
    return frontmatter, body


def _split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line[3:].strip()
            lines = []
        else:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def _numbered_items(section_text: str) -> list[str]:
    items: list[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        head = stripped.split(".", 1)
        if head[0].isdigit() and len(head) == 2:
            items.append(head[1].strip())
        elif stripped.startswith(("- ", "* ")):
            items.append(stripped[2:].strip())
    return [item for item in items if item and item != "..."]


def _repos_with_allowed_paths(frontmatter: dict[str, Any]) -> dict[str, list[str]]:
    raw_repos = frontmatter.get("repos")
    if not isinstance(raw_repos, dict) or not raw_repos:
        raise TaskSpecError("task spec must declare at least one repo under repos:")
    repos: dict[str, list[str]] = {}
    for name, config in raw_repos.items():
        if not isinstance(config, dict):
            raise TaskSpecError(f"repos.{name} must be a mapping with allowed_paths")
        allowed = config.get("allowed_paths")
        if (
            not isinstance(allowed, list)
            or not allowed
            or not all(isinstance(item, str) and item for item in allowed)
        ):
            raise TaskSpecError(f"repos.{name}.allowed_paths must be a non-empty string list")
        repos[str(name)] = [str(item) for item in allowed]
    return repos


def parse_task_spec_text(text: str) -> dict[str, Any]:
    """Parse and structurally validate a task spec document.

    Returns a plain dict (JSON-serializable, lives in ``GraphState``):
    frontmatter keys plus ``intent``, ``acceptance_criteria``, ``non_goals``,
    ``role_consult_notes``, ``rollback_sketch``.
    """
    frontmatter, body = _split_frontmatter(text)
    sections = _split_sections(body)

    change_id = frontmatter.get("change_id")
    if not isinstance(change_id, str) or not change_id:
        raise TaskSpecError("task spec must set change_id")
    repos = _repos_with_allowed_paths(frontmatter)

    criteria = _numbered_items(sections.get("Acceptance criteria", ""))
    if not criteria:
        raise TaskSpecError("task spec must list at least one testable acceptance criterion")

    intent = sections.get("Intent", "").strip()
    if not intent:
        raise TaskSpecError("task spec must state an intent")

    budget = frontmatter.get("budget")
    if budget is not None and not isinstance(budget, dict):
        raise TaskSpecError("task spec budget must be a mapping")

    required_roles = frontmatter.get("required_roles", [])
    if not isinstance(required_roles, list):
        raise TaskSpecError("task spec required_roles must be a list")

    return {
        "change_id": change_id,
        "change_class": str(frontmatter.get("change_class", "app_feature")),
        "risk_level": str(frontmatter.get("risk_level", "low")),
        "customer_impact": str(frontmatter.get("customer_impact", "none")),
        "repos": repos,
        "required_roles": [str(role) for role in required_roles],
        "gates": frontmatter.get("gates", []),
        "budget": dict(budget) if isinstance(budget, dict) else dict(DEFAULT_BUDGET),
        "intake_source": str(frontmatter.get("intake_source", "operator")),
        "intent": intent,
        "acceptance_criteria": criteria,
        "non_goals": sections.get("Non-goals", "").strip(),
        "role_consult_notes": sections.get("Role consult notes", "").strip(),
        "rollback_sketch": sections.get("Rollback sketch", "").strip(),
    }


def load_task_spec(path: str | Path) -> dict[str, Any]:
    """Load and validate a task spec file."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise TaskSpecError(f"task spec file does not exist: {resolved}")
    return parse_task_spec_text(resolved.read_text(encoding="utf-8"))


def render_task_spec(
    spec: dict[str, Any],
    *,
    role_constraints: list[dict[str, Any]] | None = None,
) -> str:
    """Render a task spec dict back to its canonical Markdown document.

    ``role_constraints`` entries ({role, constraints[], acceptance_criteria[]})
    from the plan-consult pass are rendered into the Role consult notes
    section so the spec file records every required role's input.
    """
    frontmatter = {
        "change_id": spec["change_id"],
        "change_class": spec.get("change_class", "app_feature"),
        "risk_level": spec.get("risk_level", "low"),
        "customer_impact": spec.get("customer_impact", "none"),
        "repos": {
            name: {"allowed_paths": list(paths)} for name, paths in spec.get("repos", {}).items()
        },
        "required_roles": list(spec.get("required_roles", [])),
        "gates": list(spec.get("gates", [])),
        "budget": dict(spec.get("budget", DEFAULT_BUDGET)),
        "intake_source": spec.get("intake_source", "operator"),
    }

    criteria = list(spec.get("acceptance_criteria", []))
    consult_lines: list[str] = []
    for entry in role_constraints or []:
        role = str(entry.get("role", "unknown"))
        consult_lines.append(f"### {role}")
        consult_lines.append("")
        for constraint in entry.get("constraints", []):
            consult_lines.append(f"- {constraint}")
        for criterion in entry.get("acceptance_criteria", []):
            if criterion not in criteria:
                criteria.append(criterion)
            consult_lines.append(f"- AC: {criterion}")
        consult_lines.append("")
    consult_notes = "\n".join(consult_lines).strip() or spec.get("role_consult_notes", "")

    parts = [
        "---",
        yaml.safe_dump(frontmatter, sort_keys=False).strip(),
        "---",
        "",
        "## Intent",
        "",
        str(spec.get("intent", "")).strip(),
        "",
        "## Acceptance criteria",
        "",
        "\n".join(f"{index}. {criterion}" for index, criterion in enumerate(criteria, start=1)),
        "",
        "## Done-conditions",
        "",
        "The run is complete when all acceptance criteria hold AND:",
        "",
        "- all gates in the frontmatter pass in the worktree;",
        "- the diff touches only `allowed_paths`;",
        "- every required role judgment is `approve`.",
        "",
        "## Non-goals",
        "",
        str(spec.get("non_goals", "")).strip() or "Anything outside the allowed paths.",
        "",
        "## Role consult notes",
        "",
        consult_notes,
        "",
        "## Rollback sketch",
        "",
        str(spec.get("rollback_sketch", "")).strip()
        or "Discard the generated worktree and branch; no production state changes.",
        "",
    ]
    return "\n".join(parts)


def write_task_spec(
    spec: dict[str, Any],
    path: str | Path,
    *,
    role_constraints: list[dict[str, Any]] | None = None,
) -> str:
    """Render and write the spec document; returns the resolved path."""
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        render_task_spec(spec, role_constraints=role_constraints), encoding="utf-8"
    )
    return str(resolved)
