"""Phase F (v2): the budgeted, locked, observable operations lane."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from hyrule_engineering_loop.daemon import (
    DaemonConfig,
    DaemonReport,
    acquire_lock,
    classify_issue,
    daemon_once,
    notify_discord,
    notify_icinga,
)
from hyrule_engineering_loop.intake import IntakeItem
from hyrule_engineering_loop.nodes import STALL_ROUND_LIMIT, delegate_implementation_node
from hyrule_engineering_loop.promotion import rollback_promotions, setup_worktrees_for_state
from hyrule_engineering_loop.state import GraphState


def _run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    assert completed.returncode == 0, completed.stderr


def _init_repo(path: Path, *, remote: Path | None = None) -> None:
    path.mkdir(parents=True)
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "loop@example.invalid"], path)
    _run(["git", "config", "user.name", "Engineering Loop"], path)
    (path / "README.md").write_text(f"{path.name}\n", encoding="utf-8")
    _run(["git", "add", "README.md"], path)
    _run(["git", "commit", "-m", "initial"], path)
    if remote is not None:
        _run(["git", "init", "--bare", str(remote)], path)
        _run(["git", "remote", "add", "origin", str(remote)], path)


class FakeGh:
    """Records gh calls; serves canned JSON by command prefix."""

    def __init__(self, responses: dict[str, str]) -> None:
        self.calls: list[list[str]] = []
        self.responses = responses

    def run(self, args: list[str]) -> str:
        self.calls.append(list(args))
        key = " ".join(args[:2])
        return self.responses.get(key, "[]")


def _approved_issue_json(number: int, *, repo: str, labels: list[str]) -> str:
    return json.dumps(
        [
            {
                "number": number,
                "title": "Add a docs note",
                "body": "## Context\nx\n## Action items\n1. y\n## Related\n- z",
                "labels": [{"name": name} for name in labels],
                "url": f"https://github.com/{repo}/issues/{number}",
                "updatedAt": "2026-06-12T00:00:00Z",
            }
        ]
    )


# --- AC1: run lock ----------------------------------------------------------


def test_second_invocation_exits_immediately_on_the_lock(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    # A live, fresh lock held by this very process.
    held = acquire_lock(state_dir, max_age_seconds=3600)
    assert held is not None

    config = DaemonConfig(state_dir=state_dir, output_root=tmp_path / "out")
    gh = FakeGh({"issue list": "[]"})
    report = daemon_once(config, client=gh)

    assert report.outcome == "locked"
    # Locked cycle does not even query the queue, and stays silent.
    assert gh.calls == []
    assert report.notifications == []


def test_stale_lock_is_broken(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "daemon.lock").write_text(
        json.dumps({"pid": 999_999_999, "started_at": 0.0}), encoding="utf-8"
    )
    lock = acquire_lock(state_dir, max_age_seconds=3600)
    assert lock is not None  # dead-pid lock was broken and re-taken


# --- AC: CI-runner refusal --------------------------------------------------


def test_daemon_refuses_to_run_on_ci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    config = DaemonConfig(state_dir=tmp_path / "state")
    report = daemon_once(config, client=FakeGh({}))
    assert report.outcome == "refused_ci"


# --- AC4: end-to-end seeded issue -> draft PR, no human input --------------


def test_seeded_approved_issue_becomes_draft_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _init_repo(workspace / "hyrule-cloud", remote=tmp_path / "hyrule-cloud.git")
    repo = "AS215932/hyrule-cloud"

    gh = FakeGh(
        {
            "issue list": _approved_issue_json(241, repo=repo, labels=["loop:approved", "monitoring"]),
            "issue view": json.dumps({"body": "## Context\nAdd a docs note.\n"}),
        }
    )
    monkeypatch.setenv("HYRULE_MOCK_GITHUB_PR_URL", "https://github.invalid/pr/1")

    discord: list[dict[str, Any]] = []
    icinga: list[dict[str, Any]] = []
    monkeypatch.setenv("HYRULE_DISCORD_WEBHOOK", "https://discord.invalid/webhook")
    monkeypatch.setenv("HYRULE_ICINGA_URL", "https://mon.invalid:5665")
    monkeypatch.setenv("HYRULE_ICINGA_USER", "loop")
    monkeypatch.setenv("HYRULE_ICINGA_PASSWORD", "x")

    config = DaemonConfig(
        repos=(repo,),
        workspace_root=workspace,
        output_root=tmp_path / "runs",
        state_dir=tmp_path / "state",
        memory_dir=str(tmp_path / "memory"),
    )

    report = daemon_once(
        config,
        client=gh,
        discord_poster=lambda url, payload: discord.append(payload),
        icinga_poster=lambda url, payload: icinga.append(payload),
    )

    # No human input between "timer fire" (daemon_once) and the draft PR.
    assert report.outcome == "published"
    assert report.pr_url == "https://github.invalid/pr/1"
    assert report.change_id == "ISSUE_HYRULE_CLOUD_241"

    # The branch was really pushed to the bare remote.
    branches = subprocess.run(
        ["git", "branch", "-a"],
        cwd=tmp_path / "hyrule-cloud.git",
        capture_output=True,
        text=True,
    ).stdout
    assert "hyrule-feature/" in branches

    # Reporting fired on both channels.
    assert report.notifications == ["discord", "icinga"]
    assert "published" in discord[0]["content"]
    assert icinga[0]["exit_status"] == 0

    # The ledger recorded the run.
    ledger_files = list((tmp_path / "state").glob("ledger-*.json"))
    assert ledger_files and json.loads(ledger_files[0].read_text())["runs"] == 1


def test_idle_queue_reports_idle(tmp_path: Path) -> None:
    config = DaemonConfig(
        repos=("AS215932/network-operations",),
        state_dir=tmp_path / "state",
        output_root=tmp_path / "out",
    )
    report = daemon_once(config, client=FakeGh({"issue list": "[]"}))
    assert report.outcome == "idle"


# --- AC2: per-run budget exhaustion is journaled, next run unaffected -------


def _paused_run(**kwargs: Any) -> dict[str, Any]:
    return {
        "state_path": str(kwargs["output_root"] / "state" / f"{kwargs['change_id']}.json"),
        "signoff_status": "needs_operator_triage",
        "failure_summary": {"error_excerpt": "backend budget exhausted: wall clock"},
        "final_state": {
            "backend_results": [{"cost": {"usd": 0.0}}],
            "reflection_results": {
                "written": True,
                "journal_path": str(kwargs["output_root"] / "journal.md"),
            },
        },
    }


def _published_run(**kwargs: Any) -> dict[str, Any]:
    return {
        "state_path": str(kwargs["output_root"] / "state" / f"{kwargs['change_id']}.json"),
        "signoff_status": "ready_for_review",
        "final_state": {
            "promotion_results": [{"repo": "hyrule-cloud", "branch": "b", "worktree_path": "w"}],
            "noc_handoff_path": "h",
            "backend_results": [{"cost": {"usd": 0.0}}],
            "reflection_results": {"written": True},
        },
    }


def test_budget_exhaustion_journals_and_next_run_unaffected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = "AS215932/hyrule-cloud"
    gh = FakeGh(
        {
            "issue list": _approved_issue_json(7, repo=repo, labels=["loop:approved"]),
            "issue view": json.dumps({"body": "## Context\nx\n"}),
        }
    )
    monkeypatch.setenv("HYRULE_DISCORD_WEBHOOK", "https://discord.invalid/webhook")
    config = DaemonConfig(
        repos=(repo,),
        workspace_root=tmp_path / "workspace",
        output_root=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    (tmp_path / "workspace").mkdir()

    discord: list[dict[str, Any]] = []
    report1 = daemon_once(
        config,
        client=gh,
        feature_runner=_paused_run,
        discord_poster=lambda url, payload: discord.append(payload),
    )

    assert report1.outcome == "needs_triage"
    assert report1.pr_url is None
    assert "budget exhausted" in report1.detail
    assert report1.notifications == ["discord"]
    assert "needs_triage" in discord[0]["content"]

    published: list[dict[str, Any]] = []
    report2 = daemon_once(
        config,
        client=gh,
        feature_runner=_published_run,
        publisher=lambda state, **kw: [{"github_pr": {"url": "https://github.invalid/pr/9"}}],
        discord_poster=lambda url, payload: published.append(payload),
    )

    assert report2.outcome == "published"
    assert report2.pr_url == "https://github.invalid/pr/9"

    ledger = json.loads(next((tmp_path / "state").glob("ledger-*.json")).read_text())
    assert ledger["runs"] == 2


def test_daily_run_budget_stops_further_runs(tmp_path: Path) -> None:
    repo = "AS215932/hyrule-cloud"
    config = DaemonConfig(
        repos=(repo,),
        state_dir=tmp_path / "state",
        output_root=tmp_path / "out",
        max_runs_per_day=0,
    )
    report = daemon_once(config, client=FakeGh({"issue list": "[]"}))
    assert report.outcome == "over_budget"
    assert "run budget" in report.detail


# --- kill criterion: stall detection ----------------------------------------


def test_unchanged_diff_across_rounds_aborts_to_signoff(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _init_repo(workspace / "hyrule-cloud")

    base: GraphState = cast(
        GraphState,
        {
            "change_id": "STALL_TEST",
            "change_class": "app_feature",
            "risk_level": "low",
            "customer_impact": "none",
            "source_of_truth_files": ["hyrule-cloud:README.md"],
            "proposed_mutations": {},
            "mcp_schema_breaking": False,
            "emulated_lab_verified": "not_applicable",
            "validation_errors": [],
            "role_approvals": {},
            "retry_counters": {},
            "rollback_plan": "",
            "noc_handoff_metadata": {},
            "requires_human_signoff": False,
            "promotion_enabled": True,
            "promotion_repositories": {"hyrule-cloud": str(workspace / "hyrule-cloud")},
            "promotion_allowed_paths": {"hyrule-cloud": ["docs"]},
            "promotion_worktree_root": str(tmp_path / "worktrees"),
            "promotion_branch_prefix": "hyrule-feature",
            "feature_request": "stall",
            "llm_mock_responses": {
                "implementation_writer": {
                    "approved": True,
                    "proposed_mutations": [
                        {
                            "path": "hyrule-cloud:docs/stall.md",
                            "content": "# Stall\n",
                            "operation": "create",
                        }
                    ],
                }
            },
        },
    )
    worktrees = setup_worktrees_for_state(base)
    base["worktree_results"] = worktrees

    fingerprint: str | None = None
    stall_rounds = 0
    statuses: list[str] = []
    for _ in range(STALL_ROUND_LIMIT + 1):
        state = cast(GraphState, dict(base))
        if fingerprint is not None:
            state["last_diff_fingerprint"] = fingerprint
            state["stall_rounds"] = stall_rounds
        update = delegate_implementation_node(state)
        statuses.append(update["implementation_writer_status"])
        fingerprint = update["last_diff_fingerprint"]
        stall_rounds = update["stall_rounds"]

    # First few rounds proceed; the run aborts once the diff has been
    # unchanged for STALL_ROUND_LIMIT consecutive rounds.
    assert statuses[-1] == "stalled"
    assert statuses[:STALL_ROUND_LIMIT] == ["complete"] * STALL_ROUND_LIMIT
    rollback_promotions(worktrees)


# --- reporting helpers ------------------------------------------------------


def test_classify_issue_maps_labels() -> None:
    item = IntakeItem(
        repo="r", number=1, title="t", url="u",
        labels=("firewall", "critical"), updated_at="", score=0.0, body_complete=True,
    )
    change_class, risk = classify_issue(item)
    assert change_class == "firewall_policy"
    assert risk == "high"


def test_notifications_skip_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("HYRULE_DISCORD_WEBHOOK", "HYRULE_ICINGA_URL"):
        monkeypatch.delenv(key, raising=False)
    report = DaemonReport(outcome="idle")
    assert notify_discord(report) is False
    assert notify_icinga(report) is False
