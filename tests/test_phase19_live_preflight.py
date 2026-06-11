from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from hyrule_engineering_loop.cli import main


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


def _json_from_output(output: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(output[output.index("{") :]))


def _request(path: Path, text: str = "Add a dry-live docs artifact.\n") -> Path:
    request_path = path / "request.md"
    request_path.write_text(text, encoding="utf-8")
    return request_path


def test_feature_dry_live_builds_prompt_context_without_provider(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")

    assert (
        main(
            [
                "feature",
                "DRY_LIVE",
                "--request",
                str(_request(tmp_path)),
                "--repo",
                "hyrule-cloud",
                "--workspace-root",
                str(workspace_root),
                "--output-root",
                str(tmp_path / "feature-output"),
                "--allow",
                "docs",
                "--source",
                "README.md",
                "--dry-live",
            ]
        )
        == 0
    )

    payload = _json_from_output(capsys.readouterr().out)
    preflight = cast(dict[str, object], payload["preflight"])
    writer = cast(dict[str, object], preflight["implementation_writer"])

    assert payload["dry_live"] is True
    assert payload["provider_called"] is False
    assert preflight["ok"] is True
    assert isinstance(writer["prompt_chars"], int)
    assert writer["prompt_chars"] > 0
    assert any(
        isinstance(item, dict)
        and item.get("role") == "implementation_writer"
        and item.get("model") == "moonshotai/kimi-k2.6"
        for item in cast(list[object], preflight["model_selections"])
    )


def test_feature_live_preflight_refuses_missing_provider_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("HYRULE_LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")

    exit_code = main(
        [
            "feature",
            "LIVE_PREFLIGHT",
            "--request",
            str(_request(tmp_path)),
            "--repo",
            "hyrule-cloud",
            "--workspace-root",
            str(workspace_root),
            "--output-root",
            str(tmp_path / "feature-output"),
            "--allow",
            "docs",
            "--source",
            "README.md",
            "--live",
        ]
    )

    payload = _json_from_output(capsys.readouterr().out)
    preflight = cast(dict[str, object], payload["preflight"])
    checks = cast(list[dict[str, object]], preflight["checks"])

    assert exit_code == 1
    assert payload["live_mode"] is True
    assert preflight["ok"] is False
    assert any(check["name"] == "provider_key" and check["ok"] is False for check in checks)


def test_feature_failure_summary_reports_next_operator_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")

    assert (
        main(
            [
                "feature",
                "FAILURE_UX",
                "--request",
                str(_request(tmp_path, "Replace existing README incorrectly.\n")),
                "--repo",
                "hyrule-cloud",
                "--workspace-root",
                str(workspace_root),
                "--output-root",
                str(tmp_path / "feature-output"),
                "--allow",
                "README.md",
                "--mock-mutation",
                "README.md=bad create\n",
            ]
        )
        == 0
    )

    payload = _json_from_output(capsys.readouterr().out)
    failure = cast(dict[str, object], payload["failure_summary"])

    assert payload["requires_human_signoff"] is True
    assert failure["last_failing_node"] == "promotion"
    assert failure["retry_count"] == 3
    assert "create mutation target already exists" in str(failure["error_excerpt"])
    assert "hyrule-engineering-loop trace --state-path" in str(failure["next_operator_command"])


def test_writer_canary_dry_live(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")

    assert (
        main(
            [
                "writer-canary",
                "--workspace-root",
                str(workspace_root),
                "--repo-name",
                "hyrule-cloud",
                "--output-root",
                str(tmp_path / "writer-canary"),
                "--dry-live",
            ]
        )
        == 0
    )

    payload = _json_from_output(capsys.readouterr().out)
    preflight = cast(dict[str, object], payload["preflight"])

    assert payload["dry_live"] is True
    assert payload["provider_called"] is False
    assert preflight["ok"] is True
