from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

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


def _write_model_policy(path: Path) -> Path:
    path.write_text(
        """
version: 1
defaults:
  provider: openrouter
  model: minimax/minimax-m3
  tier: cheap
roles:
  systems_engineer:
    provider: openrouter
    model: moonshotai/kimi-k2.6
    tier: mid
  devops_netops:
    provider: openrouter
    model: moonshotai/kimi-k2.6
    tier: mid
risk_overrides:
  high:
    min_tier: strong
retry_escalation:
  after_failures: 1
  max_tier: frontier
tier_fallbacks:
  strong:
    provider: anthropic
    model: claude-sonnet-4-6
  frontier:
    provider: openai
    model: gpt-5.5
""".lstrip(),
        encoding="utf-8",
    )
    return path


def _json_from_output(output: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(output[output.index("{") :]))


def test_models_show_and_validate_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path = _write_model_policy(tmp_path / "model-policy.yml")

    assert main(["models", "show", "--model-policy", str(policy_path), "--risk-level", "high", "--json"]) == 0
    show_payload = _json_from_output(capsys.readouterr().out)
    roles = cast(list[Any], show_payload["roles"])
    systems = next(
        item
        for item in roles
        if isinstance(item, dict) and item.get("role") == "systems_engineer"
    )
    assert systems["provider"] == "anthropic"
    assert systems["model"] == "claude-sonnet-4-6"
    assert systems["tier"] == "strong"

    monkeypatch.setenv("HYRULE_LLM_API_KEY", "test-key")
    assert main(["models", "validate", "--model-policy", str(policy_path), "--require-keys", "--json"]) == 0
    validate_payload = _json_from_output(capsys.readouterr().out)
    assert validate_payload["ok"] is True
    assert validate_payload["errors"] == []


def test_feature_summary_includes_model_selection_and_trace_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Add a model summary to the loop UX.\n", encoding="utf-8")
    policy_path = _write_model_policy(tmp_path / "model-policy.yml")

    assert (
        main(
            [
                "feature",
                "MODEL_UX",
                "--request",
                str(request_path),
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
                "--model-policy",
                str(policy_path),
            ]
        )
        == 0
    )
    summary = _json_from_output(capsys.readouterr().out)
    model_summary = summary["model_summary"]
    assert isinstance(model_summary, list)
    assert any(
        isinstance(item, dict)
        and item.get("role") == "systems_engineer"
        and isinstance(item.get("model_selection"), dict)
        and item["model_selection"].get("model") == "moonshotai/kimi-k2.6"
        for item in model_summary
    )

    state = json.loads(Path(str(summary["state_path"])).read_text(encoding="utf-8"))
    assert state["model_policy_file"] == str(policy_path)

    assert main(["trace", "--trace-path", str(summary["trace_path"])]) == 0
    trace_output = capsys.readouterr().out
    assert "role_models:" in trace_output
    assert "systems_engineer: openrouter/moonshotai/kimi-k2.6 tier=mid" in trace_output

    assert main(["state-cleanup", "--state-path", str(summary["state_path"])]) == 0
