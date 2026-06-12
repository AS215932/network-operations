"""Read-only signal miners: infrastructure telemetry into candidate issues.

Each miner observes one signal source — Icinga unhandled problems,
Prometheus firing alerts, nightly drift-detection artifacts,
``netops-nightly`` failures — and emits :class:`Signal` objects. Miners are
strictly read-only (HTTP GETs and ``gh run list`` queries); the only write
anywhere in intake is candidate-issue creation, and a signal already
represented by an open issue files nothing (fingerprint dedupe).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, TypeAlias

from hyrule_engineering_loop.intake.github_issues import (
    GhClient,
    IntakeReport,
    file_candidate_issue,
    find_fingerprint_issue,
    signal_fingerprint,
)

HttpGet: TypeAlias = Callable[[str, dict[str, str]], str]

DEFAULT_WORKFLOWS = ("drift-detection.yml", "netops-nightly.yml")
MAX_SIGNALS_PER_SOURCE = 10


@dataclass(frozen=True)
class Signal:
    """One actionable observation from a read-only miner."""

    source: str
    identifier: str
    title: str
    context: str
    action_items: tuple[str, ...]
    related: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return signal_fingerprint(self.source, self.identifier)


def _default_http_get(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return str(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc


def mine_icinga(http_get: HttpGet | None = None) -> tuple[list[Signal], str | None]:
    """Unhandled, non-acknowledged service problems from the Icinga API."""
    url = os.environ.get("HYRULE_ICINGA_URL")
    user = os.environ.get("HYRULE_ICINGA_USER")
    password = os.environ.get("HYRULE_ICINGA_PASSWORD")
    if not url or not user or not password:
        return [], "icinga: HYRULE_ICINGA_URL/USER/PASSWORD not configured"

    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    raw = (http_get or _default_http_get)(
        f"{url.rstrip('/')}/v1/objects/services"
        "?filter=service.state!=0%26%26service.acknowledgement==0",
        {"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    decoded = json.loads(raw)
    signals: list[Signal] = []
    for result in decoded.get("results", [])[:MAX_SIGNALS_PER_SOURCE]:
        attrs = result.get("attrs", {}) if isinstance(result, dict) else {}
        name = str(attrs.get("__name") or attrs.get("name") or "unknown-service")
        state = attrs.get("state")
        signals.append(
            Signal(
                source="icinga",
                identifier=name,
                title=f"Icinga unhandled problem: {name}",
                context=(
                    f"The Icinga service check `{name}` is in state {state} and is "
                    "neither acknowledged nor in a downtime. The Engineering Loop "
                    "intake scan flagged it as a candidate for engineering work "
                    "(recurring problems usually need a config or code change, "
                    "not another ack)."
                ),
                action_items=(
                    f"Investigate why `{name}` keeps failing.",
                    "Decide: fix the underlying config/code, tune the check, or "
                    "document the expected state.",
                ),
                related=(f"icinga service `{name}`", "monitoring role / host_vars"),
            )
        )
    return signals, None


def mine_prometheus(http_get: HttpGet | None = None) -> tuple[list[Signal], str | None]:
    """Firing Prometheus alerts via the HTTP API."""
    url = os.environ.get("HYRULE_PROMETHEUS_URL")
    if not url:
        return [], "prometheus: HYRULE_PROMETHEUS_URL not configured"

    raw = (http_get or _default_http_get)(
        f"{url.rstrip('/')}/api/v1/alerts", {"Accept": "application/json"}
    )
    decoded = json.loads(raw)
    alerts = decoded.get("data", {}).get("alerts", [])
    signals: list[Signal] = []
    for alert in alerts[:MAX_SIGNALS_PER_SOURCE]:
        if not isinstance(alert, dict) or alert.get("state") != "firing":
            continue
        labels = alert.get("labels", {}) if isinstance(alert.get("labels"), dict) else {}
        name = str(labels.get("alertname", "unknown-alert"))
        instance = str(labels.get("instance", ""))
        identifier = f"{name}@{instance}" if instance else name
        signals.append(
            Signal(
                source="prometheus",
                identifier=identifier,
                title=f"Prometheus alert firing: {identifier}",
                context=(
                    f"The Prometheus alert `{name}`"
                    + (f" on `{instance}`" if instance else "")
                    + " is firing. If this is a recurring breach it likely needs an "
                    "engineering change (capacity, config, or alert tuning)."
                ),
                action_items=(
                    f"Check the alert expression and recent history for `{name}`.",
                    "Fix the underlying cause or tune the rule with a justification.",
                ),
                related=("configs/mon/prometheus.yml", f"alert `{name}`"),
            )
        )
    return signals, None


def mine_workflow_failures(
    *,
    repo: str,
    client: GhClient,
    workflows: tuple[str, ...] = DEFAULT_WORKFLOWS,
) -> tuple[list[Signal], str | None]:
    """Most recent failed run per nightly workflow (read-only ``gh run list``)."""
    signals: list[Signal] = []
    for workflow in workflows:
        raw = client.run(
            [
                "run",
                "list",
                "--repo",
                repo,
                "--workflow",
                workflow,
                "--limit",
                "1",
                "--json",
                "conclusion,displayTitle,url,createdAt",
            ]
        )
        try:
            runs = json.loads(raw or "[]")
        except json.JSONDecodeError:
            continue
        for run in runs if isinstance(runs, list) else []:
            if not isinstance(run, dict) or run.get("conclusion") != "failure":
                continue
            created = str(run.get("createdAt", ""))[:10]
            signals.append(
                Signal(
                    source="nightly",
                    identifier=f"{workflow}:{created}",
                    title=f"Nightly workflow failed: {workflow} ({created})",
                    context=(
                        f"The latest `{workflow}` run failed on {created}. Nightly "
                        "failures are detection, not pre-merge proof — the drift or "
                        "breakage they detect needs an engineering change."
                    ),
                    action_items=(
                        f"Open the failed run and identify the failing step: {run.get('url')}",
                        "File or fix the underlying drift/config issue.",
                    ),
                    related=(str(run.get("url", "")), f".github/workflows/{workflow}"),
                )
            )
    return signals, None


def mine_all_signals(
    *,
    repo: str,
    client: GhClient,
    http_get: HttpGet | None = None,
) -> tuple[list[Signal], list[str]]:
    """Run every configured miner; unconfigured sources skip gracefully."""
    signals: list[Signal] = []
    skipped: list[str] = []
    miners: tuple[Callable[[], tuple[list[Signal], str | None]], ...] = (
        lambda: mine_icinga(http_get),
        lambda: mine_prometheus(http_get),
        lambda: mine_workflow_failures(repo=repo, client=client),
    )
    for miner in miners:
        try:
            mined, note = miner()
        except Exception as exc:
            skipped.append(f"miner error: {exc}")
            continue
        signals.extend(mined)
        if note:
            skipped.append(note)
    return signals, skipped


def signals_to_candidates(
    signals: list[Signal],
    *,
    repo: str,
    client: GhClient,
    dry_run: bool = False,
) -> IntakeReport:
    """Dedupe signals against open issues and file the new ones as candidates."""
    report = IntakeReport(dry_run=dry_run)
    for signal in signals:
        existing = find_fingerprint_issue(repo, signal.fingerprint, client=client)
        if existing is not None:
            report.deduplicated.append(
                {
                    "title": signal.title,
                    "fingerprint": signal.fingerprint,
                    "existing_issue": existing,
                }
            )
            continue
        entry: dict[str, Any] = {
            "title": signal.title,
            "fingerprint": signal.fingerprint,
            "repo": repo,
            "source": signal.source,
        }
        if not dry_run:
            entry["url"] = file_candidate_issue(
                repo=repo,
                title=signal.title,
                context=signal.context,
                action_items=list(signal.action_items),
                related=list(signal.related),
                fingerprint=signal.fingerprint,
                client=client,
            )
        report.filed.append(entry)
    return report
