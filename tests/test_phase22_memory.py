"""Phase D (v2): memory tree, reflection node, and the lesson flywheel."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from hyrule_engineering_loop.backend import assemble_backend_prompt, constraints_from_state, task_spec_from_state
from hyrule_engineering_loop.cli import main
from hyrule_engineering_loop.feature import build_feature_state
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.promotion import rollback_promotions
from hyrule_engineering_loop.state import GraphState


def _run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    assert completed.returncode == 0, completed.stderr


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "loop@example.invalid"], path)
    _run(["git", "config", "user.name", "Engineering Loop"], path)
    (path / "README.md").write_text(f"{path.name}\n", encoding="utf-8")
    _run(["git", "add", "README.md"], path)
    _run(["git", "commit", "-m", "initial"], path)


def _feature_state(
    tmp_path: Path,
    change_id: str,
    *,
    memory_dir: Path,
    gate_command: list[str] | None = None,
) -> GraphState:
    workspace_root = tmp_path / "workspace"
    if not (workspace_root / "hyrule-cloud").exists():
        _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Exercise the memory flywheel.\n", encoding="utf-8")
    return build_feature_state(
        change_id=change_id,
        change_class="app_feature",
        workspace_root=workspace_root,
        output_root=tmp_path / "out" / change_id.lower(),
        repo_name="hyrule-cloud",
        request_path=request_path,
        allowed_paths=["docs"],
        source_files=["README.md"],
        gate_command=gate_command,
        memory_dir=str(memory_dir),
    )


def test_completed_run_writes_exactly_one_journal_entry(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    state = _feature_state(tmp_path, "JOURNAL_OK", memory_dir=memory_root)

    final_state = dict(build_graph().invoke(state))

    assert final_state["promotion_status"] == "passed"
    reflection = final_state["reflection_results"]
    assert reflection["written"] is True
    journal_files = sorted((memory_root / "journal").glob("*.md"))
    assert [path.name for path in journal_files] == ["JOURNAL_OK.md"]
    journal = journal_files[0].read_text(encoding="utf-8")
    assert "repos: hyrule-cloud" in journal
    assert "promotion_status: passed" in journal
    # A clean run proposes no lessons.
    assert not (memory_root / "proposals").exists()
    assert not (memory_root / "lessons").exists()

    rollback_promotions(final_state["promotion_results"])


def test_repeated_gate_failure_produces_lesson_proposal(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    failing_gate = ["python", "-c", "import sys; sys.stderr.write('flaky assertion'); sys.exit(1)"]
    state = _feature_state(
        tmp_path, "GATE_REPEAT", memory_dir=memory_root, gate_command=failing_gate
    )

    final_state = dict(build_graph().invoke(state))

    # Signed-off failure run still writes exactly one journal entry (AC1).
    assert final_state["requires_human_signoff"] is True
    journal_files = sorted((memory_root / "journal").glob("*.md"))
    assert [path.name for path in journal_files] == ["GATE_REPEAT.md"]

    # AC2: the proposal names the gate and the failing pattern.
    proposal_files = sorted((memory_root / "proposals").glob("*.md"))
    assert [path.name for path in proposal_files] == ["GATE_REPEAT.md"]
    proposal = proposal_files[0].read_text(encoding="utf-8")
    assert "gate:" in proposal
    assert "import sys; sys.stderr.write('flaky assertion'); sys.exit(1)" in proposal
    assert "flaky assertion" in proposal
    assert "memory/lessons/hyrule-cloud.md" in proposal
    assert "memory/journal/GATE_REPEAT.md" in proposal

    # AC3: the loop never writes the rulebook directly.
    assert not (memory_root / "lessons").exists()


def test_lesson_appears_verbatim_in_backend_context(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    lesson_line = "- Never bind exporters to wildcard addresses; use the infra address."
    lessons_file = memory_root / "lessons" / "hyrule-cloud.md"
    lessons_file.parent.mkdir(parents=True)
    lessons_file.write_text(f"# Lessons: hyrule-cloud\n\n{lesson_line}\n", encoding="utf-8")

    state = _feature_state(tmp_path, "LESSON_INJECT", memory_dir=memory_root)
    spec = task_spec_from_state(state)
    assert spec.lessons["hyrule-cloud"].find(lesson_line) >= 0

    prompt = assemble_backend_prompt(spec, constraints_from_state(state))
    assert lesson_line in prompt
    assert "## Lessons for hyrule-cloud" in prompt


def test_dry_live_preview_reports_lessons_injection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory_root = tmp_path / "memory"
    lessons_file = memory_root / "lessons" / "hyrule-cloud.md"
    lessons_file.parent.mkdir(parents=True)
    lessons_file.write_text("- Planted lesson.\n", encoding="utf-8")
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Dry-live lessons preview.\n", encoding="utf-8")

    assert (
        main(
            [
                "feature",
                "LESSON_PREVIEW",
                "--request",
                str(request_path),
                "--repo",
                "hyrule-cloud",
                "--workspace-root",
                str(workspace_root),
                "--output-root",
                str(tmp_path / "out"),
                "--allow",
                "docs",
                "--memory-dir",
                str(memory_root),
                "--dry-live",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    payload = cast(dict[str, Any], json.loads(output[output.index("{") :]))
    backend = cast(dict[str, Any], cast(dict[str, Any], payload["preflight"])["backend"])
    assert backend["lessons_injected"] == {"hyrule-cloud": 18}


def test_journal_tail_reaches_next_run_for_same_repo(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    failing_gate = ["python", "-c", "import sys; sys.exit(1)"]
    first = _feature_state(tmp_path, "RUN_ONE", memory_dir=memory_root, gate_command=failing_gate)
    dict(build_graph().invoke(first))
    assert (memory_root / "journal" / "RUN_ONE.md").exists()

    second = _feature_state(tmp_path, "RUN_TWO", memory_dir=memory_root)
    spec = task_spec_from_state(second)
    assert "RUN_ONE" in spec.journal_tail

    prompt = assemble_backend_prompt(spec, constraints_from_state(second))
    assert "Recent run journal" in prompt
    assert "RUN_ONE" in prompt


def test_reflection_skips_when_memory_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HYRULE_MEMORY_DIR", raising=False)
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("No memory configured.\n", encoding="utf-8")
    state = build_feature_state(
        change_id="NO_MEMORY",
        change_class="app_feature",
        workspace_root=workspace_root,
        output_root=tmp_path / "out",
        repo_name="hyrule-cloud",
        request_path=request_path,
        allowed_paths=["docs"],
        source_files=["README.md"],
    )

    final_state = dict(build_graph().invoke(state))

    assert final_state["reflection_results"]["written"] is False
    assert "journal_path" not in final_state["reflection_results"]

    rollback_promotions(final_state["promotion_results"])


def test_lessons_cli_lists_lessons_and_proposals(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory_root = tmp_path / "memory"
    (memory_root / "lessons").mkdir(parents=True)
    (memory_root / "lessons" / "hyrule-cloud.md").write_text("- A rule.\n", encoding="utf-8")
    (memory_root / "proposals").mkdir(parents=True)
    (memory_root / "proposals" / "OLD_RUN.md").write_text(
        "# Lesson proposal: OLD_RUN\n", encoding="utf-8"
    )

    assert main(["lessons", "--memory-dir", str(memory_root), "--json"]) == 0
    payload = cast(dict[str, Any], json.loads(capsys.readouterr().out))

    assert [item["name"] for item in payload["lessons"]] == ["hyrule-cloud"]
    assert [item["name"] for item in payload["proposals"]] == ["OLD_RUN"]
    assert payload["journal_count"] == 0
