"""Operations lane — scheduled, budgeted, one-item-at-a-time autonomy.

Phase F of the v2 architecture (``docs/engineering-loop/v2-architecture.md``
§9). ``daemon_once`` runs one cycle: acquire the run lock, check the per-day
budget ledger, pick exactly one ``loop:approved`` issue (highest triage
score), run the full graph, and either publish a **draft PR** (clean run —
the human pre-authorized the work by applying the label; merge stays
human-gated) or leave a journaled failure for triage. Every cycle reports a
one-line summary to Discord and a passive check result to Icinga, then
exits.

Hard rules: one run at a time (pid lock with stale detection); per-run and
per-day budgets; the backend never executes on a CI runner — the daemon
refuses outright when ``GITHUB_ACTIONS`` is set.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TypeAlias

from hyrule_engineering_loop.feature import run_feature_intake
from hyrule_engineering_loop.intake import (
    APPROVED_LABEL,
    GhClient,
    IntakeItem,
    list_issues_with_label,
)
from hyrule_engineering_loop.pr import publish_promoted_worktrees
from hyrule_engineering_loop.state import ChangeClass, RiskLevel

Poster: TypeAlias = Callable[[str, dict[str, Any]], None]
FeatureRunner: TypeAlias = Callable[..., dict[str, Any]]
Publisher: TypeAlias = Callable[..., list[dict[str, Any]]]

DEFAULT_LOCK_MAX_AGE_SECONDS = 2 * 60 * 60

LABEL_CHANGE_CLASSES: dict[str, ChangeClass] = {
    "bug": "app_bugfix",
    "firewall": "firewall_policy",
    "bgp": "routing_bgp_frr",
    "ospf": "routing_bgp_frr",
    "dns": "dns",
    "monitoring": "monitoring_logging",
    "ansible": "infra_ansible",
}
HIGH_RISK_LABELS = frozenset({"critical", "security"})


class DaemonError(RuntimeError):
    """Raised when a daemon cycle cannot run at all."""


@dataclass(frozen=True)
class DaemonConfig:
    """One-cycle configuration; everything has an env-overridable default."""

    repos: tuple[str, ...] = ("AS215932/network-operations",)
    workspace_root: Path = Path("/home/svag/Dev")
    output_root: Path = Path("/tmp/hyrule-loop-daemon")
    state_dir: Path = Path(".engineering-loop-state/daemon")
    memory_dir: str | None = None
    allowed_paths: tuple[str, ...] = ("docs",)
    remote: str = "origin"
    max_runs_per_day: int = 6
    max_cost_usd_per_day: float = 25.0
    max_iterations_per_run: int = 20
    max_wall_clock_minutes_per_run: int = 45
    max_cost_usd_per_run: float = 5.0
    lock_max_age_seconds: int = DEFAULT_LOCK_MAX_AGE_SECONDS


@dataclass
class DaemonReport:
    """Outcome of one daemon cycle."""

    outcome: str
    detail: str = ""
    issue: dict[str, Any] | None = None
    change_id: str | None = None
    state_path: str | None = None
    pr_url: str | None = None
    journal_path: str | None = None
    cost_usd: float = 0.0
    wall_clock_seconds: float = 0.0
    notifications: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "detail": self.detail,
            "issue": self.issue,
            "change_id": self.change_id,
            "state_path": self.state_path,
            "pr_url": self.pr_url,
            "journal_path": self.journal_path,
            "cost_usd": round(self.cost_usd, 4),
            "wall_clock_seconds": round(self.wall_clock_seconds, 1),
            "notifications": self.notifications,
        }


# --- lock ---------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(state_dir: Path, *, max_age_seconds: int) -> Path | None:
    """Take the run lock; return None when another live run holds it."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "daemon.lock"
    if lock_path.exists():
        try:
            holder = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = int(holder.get("pid", -1))
            started = float(holder.get("started_at", 0.0))
        except (json.JSONDecodeError, ValueError):
            pid, started = -1, 0.0
        fresh = (time.time() - started) < max_age_seconds
        if pid > 0 and fresh and _pid_alive(pid):
            return None
        # Stale lock: holder is dead or too old — break it.
        lock_path.unlink(missing_ok=True)
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": time.time()}),
        encoding="utf-8",
    )
    return lock_path


def release_lock(lock_path: Path | None) -> None:
    if lock_path is not None:
        lock_path.unlink(missing_ok=True)


# --- per-day ledger -------------------------------------------------------


def _ledger_path(state_dir: Path, day: str) -> Path:
    return state_dir / f"ledger-{day}.json"


def load_ledger(state_dir: Path, day: str) -> dict[str, Any]:
    path = _ledger_path(state_dir, day)
    if not path.exists():
        return {"runs": 0, "cost_usd": 0.0, "wall_clock_seconds": 0.0}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {"runs": 0, "cost_usd": 0.0, "wall_clock_seconds": 0.0}


def update_ledger(
    state_dir: Path, day: str, *, cost_usd: float, wall_clock_seconds: float
) -> dict[str, Any]:
    ledger = load_ledger(state_dir, day)
    ledger["runs"] = int(ledger.get("runs", 0)) + 1
    ledger["cost_usd"] = float(ledger.get("cost_usd", 0.0)) + cost_usd
    ledger["wall_clock_seconds"] = (
        float(ledger.get("wall_clock_seconds", 0.0)) + wall_clock_seconds
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    _ledger_path(state_dir, day).write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return ledger


# --- reporting ------------------------------------------------------------


def _default_http_post(url: str, payload: dict[str, Any]) -> None:
    headers = {"Content-Type": "application/json"}
    auth = payload.pop("_basic_auth", None)
    if isinstance(auth, str):
        headers["Authorization"] = f"Basic {auth}"
    headers.update(payload.pop("_headers", {}))
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20):
        pass


def notify_discord(report: DaemonReport, *, poster: Poster | None = None) -> bool:
    """One-line run summary to the configured Discord webhook."""
    webhook = os.environ.get("HYRULE_DISCORD_WEBHOOK")
    if not webhook:
        return False
    parts = [f"engineering-loop: {report.outcome}"]
    if report.change_id:
        parts.append(report.change_id)
    if report.pr_url:
        parts.append(report.pr_url)
    if report.detail:
        parts.append(report.detail)
    parts.append(f"cost=${report.cost_usd:.2f} wall={int(report.wall_clock_seconds)}s")
    (poster or _default_http_post)(webhook, {"content": " | ".join(parts)})
    return True


ICINGA_EXIT_STATUS: dict[str, int] = {
    "published": 0,
    "idle": 0,
    "needs_triage": 1,
    "over_budget": 1,
    "locked": 0,
    "refused_ci": 2,
    "error": 2,
}


def notify_icinga(report: DaemonReport, *, poster: Poster | None = None) -> bool:
    """Submit a passive check result; freshness alerting is Icinga-side config."""
    url = os.environ.get("HYRULE_ICINGA_URL")
    user = os.environ.get("HYRULE_ICINGA_USER")
    password = os.environ.get("HYRULE_ICINGA_PASSWORD")
    check = os.environ.get("HYRULE_ICINGA_CHECK", "noc!engineering-loop")
    if not url or not user or not password:
        return False
    payload: dict[str, Any] = {
        "type": "Service",
        "filter": f'service.__name=="{check}"',
        "exit_status": ICINGA_EXIT_STATUS.get(report.outcome, 2),
        "plugin_output": (
            f"loop {report.outcome}"
            + (f": {report.change_id}" if report.change_id else "")
            + (f" ({report.detail})" if report.detail else "")
        ),
        "_basic_auth": b64encode(f"{user}:{password}".encode()).decode(),
        "_headers": {"Accept": "application/json"},
    }
    (poster or _default_http_post)(
        f"{url.rstrip('/')}/v1/actions/process-check-result", payload
    )
    return True


# --- issue -> run mapping ---------------------------------------------------


def classify_issue(item: IntakeItem) -> tuple[ChangeClass, RiskLevel]:
    """Map issue labels onto a change class and risk level."""
    change_class: ChangeClass = "app_feature"
    for label in item.labels:
        mapped = LABEL_CHANGE_CLASSES.get(label.lower())
        if mapped is not None:
            change_class = mapped
            break
    risk: RiskLevel = (
        "high" if any(label.lower() in HIGH_RISK_LABELS for label in item.labels) else "low"
    )
    return change_class, risk


def repo_name_for_issue(item: IntakeItem) -> str:
    """Map a GitHub repo onto the sibling checkout directory name."""
    short = item.repo.rsplit("/", 1)[-1]
    if short == "network-operations":
        return "hyrule-infra"
    return short


def _issue_body(item: IntakeItem, *, client: GhClient) -> str:
    raw = client.run(
        ["issue", "view", str(item.number), "--repo", item.repo, "--json", "body"]
    )
    try:
        decoded = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return ""
    return str(decoded.get("body", "")) if isinstance(decoded, dict) else ""


def _change_id_for(item: IntakeItem) -> str:
    repo_slug = item.repo.rsplit("/", 1)[-1].upper().replace("-", "_")
    return f"ISSUE_{repo_slug}_{item.number}"


def _run_cost(final_state: dict[str, Any]) -> float:
    return sum(
        float(run.get("cost", {}).get("usd") or 0.0)
        for run in final_state.get("backend_results", [])
        if isinstance(run, dict)
    )


# --- the cycle --------------------------------------------------------------


def daemon_once(
    config: DaemonConfig,
    *,
    client: GhClient,
    feature_runner: FeatureRunner | None = None,
    publisher: Publisher | None = None,
    discord_poster: Poster | None = None,
    icinga_poster: Poster | None = None,
) -> DaemonReport:
    """Run one autonomous cycle: pick one approved item, run, publish or journal."""
    started = time.monotonic()
    if os.environ.get("GITHUB_ACTIONS"):
        return _finish(
            DaemonReport(
                outcome="refused_ci",
                detail="backend execution never runs on a CI runner",
            ),
            discord_poster,
            icinga_poster,
        )

    state_dir = config.state_dir.expanduser().resolve()
    lock_path = acquire_lock(state_dir, max_age_seconds=config.lock_max_age_seconds)
    if lock_path is None:
        # Deliberately no notifications: a held lock means another cycle is
        # already reporting; double-reporting would mask real staleness.
        return DaemonReport(outcome="locked", detail="another run holds the lock")

    try:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        ledger = load_ledger(state_dir, day)
        if int(ledger.get("runs", 0)) >= config.max_runs_per_day:
            return _finish(
                DaemonReport(
                    outcome="over_budget",
                    detail=f"daily run budget reached ({config.max_runs_per_day})",
                ),
                discord_poster,
                icinga_poster,
            )
        if float(ledger.get("cost_usd", 0.0)) >= config.max_cost_usd_per_day:
            return _finish(
                DaemonReport(
                    outcome="over_budget",
                    detail=f"daily cost budget reached (${config.max_cost_usd_per_day:.2f})",
                ),
                discord_poster,
                icinga_poster,
            )

        queue = list_issues_with_label(list(config.repos), APPROVED_LABEL, client=client)
        if not queue:
            return _finish(
                DaemonReport(outcome="idle", detail="approved queue is empty"),
                discord_poster,
                icinga_poster,
            )
        item = queue[0]
        change_class, risk = classify_issue(item)
        change_id = _change_id_for(item)
        body = _issue_body(item, client=client)

        output_root = config.output_root.expanduser().resolve() / change_id.lower()
        output_root.mkdir(parents=True, exist_ok=True)
        request_path = output_root / "request.md"
        request_path.write_text(
            f"# {item.title}\n\n"
            f"- source issue: {item.url}\n"
            f"- labels: {', '.join(item.labels)}\n\n"
            f"{body}\n",
            encoding="utf-8",
        )

        runner = feature_runner or run_feature_intake
        result = runner(
            change_id=change_id,
            change_class=change_class,
            workspace_root=config.workspace_root,
            output_root=output_root,
            repo_name=repo_name_for_issue(item),
            request_path=request_path,
            allowed_paths=list(config.allowed_paths),
            source_files=["README.md"],
            memory_dir=config.memory_dir,
            backend_budget={
                "max_iterations": config.max_iterations_per_run,
                "max_wall_clock_minutes": config.max_wall_clock_minutes_per_run,
                "max_cost_usd": config.max_cost_usd_per_run,
            },
        )
        final_state = dict(result.get("final_state", {}))
        final_state["risk_level"] = final_state.get("risk_level", risk)
        cost = _run_cost(final_state)
        report = DaemonReport(
            outcome="needs_triage",
            issue={"repo": item.repo, "number": item.number, "title": item.title},
            change_id=change_id,
            state_path=str(result.get("state_path")),
            cost_usd=cost,
            journal_path=(final_state.get("reflection_results") or {}).get("journal_path"),
        )

        if result.get("signoff_status") == "ready_for_review" and final_state.get(
            "promotion_results"
        ):
            # The human pre-authorized this work by applying loop:approved;
            # publication still ends at a DRAFT PR — merge stays human.
            publish_state = {
                **final_state,
                "approval_decision": "approved",
                "requires_human_signoff": False,
            }
            publish = publisher or publish_promoted_worktrees
            pr_results = publish(
                publish_state,
                remote=config.remote,
                commit_message=f"{change_id}: {item.title}",
                pr_title=item.title,
                pr_body=f"Closes {item.url}",
                pr_labels=[],
                pr_reviewers=[],
                create_github_pr=True,
            )
            report.outcome = "published"
            github = pr_results[0].get("github_pr", {}) if pr_results else {}
            report.pr_url = github.get("url") if isinstance(github, dict) else None
            report.detail = f"draft PR from {item.repo}#{item.number}"
        else:
            failure = result.get("failure_summary") or {}
            report.detail = str(
                failure.get("error_excerpt", "run paused for operator triage")
            )[:200]

        report.wall_clock_seconds = time.monotonic() - started
        update_ledger(
            state_dir,
            day,
            cost_usd=report.cost_usd,
            wall_clock_seconds=report.wall_clock_seconds,
        )
        return _finish(report, discord_poster, icinga_poster)
    except Exception as exc:
        report = DaemonReport(outcome="error", detail=str(exc)[:300])
        report.wall_clock_seconds = time.monotonic() - started
        return _finish(report, discord_poster, icinga_poster)
    finally:
        release_lock(lock_path)


def _finish(
    report: DaemonReport,
    discord_poster: Poster | None,
    icinga_poster: Poster | None,
) -> DaemonReport:
    try:
        if notify_discord(report, poster=discord_poster):
            report.notifications.append("discord")
    except Exception as exc:
        report.notifications.append(f"discord_failed: {exc}")
    try:
        if notify_icinga(report, poster=icinga_poster):
            report.notifications.append("icinga")
    except Exception as exc:
        report.notifications.append(f"icinga_failed: {exc}")
    return report
