"""GitHub-issue intake: the label-gated triage inbox.

Label protocol (v2 architecture §8):

- ``loop:candidate`` — machine-proposed work awaiting human triage. The
  only label this package ever applies.
- ``loop:approved`` — human-blessed work, eligible for autonomous runs
  (consumed by the Phase F operations lane). **Nothing here can apply it**;
  a human relabels candidates after review.

All GitHub access goes through the ``gh`` CLI behind a small client
protocol so tests run fully offline against a fake.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

CANDIDATE_LABEL = "loop:candidate"
APPROVED_LABEL = "loop:approved"

FINGERPRINT_MARKER = "loop-fingerprint:"

# Deterministic, documented scoring weights for queue ordering.
LABEL_SCORE_WEIGHTS: dict[str, float] = {
    "critical": 5.0,
    "security": 4.0,
    "bug": 2.0,
    "firewall": 2.0,
    "bgp": 2.0,
    "monitoring": 1.0,
    "routine": 1.0,
}
MAX_AGE_SCORE = 2.0


class IntakeError(RuntimeError):
    """Raised when the intake layer cannot complete an operation."""


class GhClient(Protocol):
    """Minimal ``gh`` invocation surface; fakes implement this in tests."""

    def run(self, args: list[str]) -> str:
        """Run ``gh <args>`` and return stdout; raise ``IntakeError`` on failure."""
        ...


class GhCli:
    """Real ``gh`` CLI client."""

    def run(self, args: list[str]) -> str:
        completed = subprocess.run(
            ["gh", *args],
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise IntakeError(
                completed.stderr.strip() or completed.stdout.strip() or "gh failed"
            )
        return completed.stdout


@dataclass(frozen=True)
class IntakeItem:
    """One scored issue from the triage inbox."""

    repo: str
    number: int
    title: str
    url: str
    labels: tuple[str, ...]
    updated_at: str
    score: float
    body_complete: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "labels": list(self.labels),
            "updated_at": self.updated_at,
            "score": round(self.score, 2),
            "body_complete": self.body_complete,
        }


def signal_fingerprint(source: str, identifier: str) -> str:
    """Stable fingerprint embedded in candidate bodies for dedupe."""
    return hashlib.sha256(f"{source}:{identifier}".encode()).hexdigest()[:16]


def _body_complete(body: str) -> bool:
    return all(section in body for section in ("## Context", "## Action items", "## Related"))


def _age_days(updated_at: str) -> float:
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (datetime.now(UTC) - updated).total_seconds() / 86_400)


def score_issue(*, labels: list[str], body: str, updated_at: str) -> float:
    """Deterministic triage score: label weights + bounded age + body quality."""
    score = 1.0
    for label in labels:
        score += LABEL_SCORE_WEIGHTS.get(label.lower(), 0.0)
    score += min(MAX_AGE_SCORE, _age_days(updated_at) / 30.0)
    if _body_complete(body):
        score += 1.0
    return score


def list_issues_with_label(
    repos: list[str],
    label: str,
    *,
    client: GhClient,
) -> list[IntakeItem]:
    """List open issues carrying ``label`` across repos, highest score first."""
    items: list[IntakeItem] = []
    for repo in repos:
        raw = client.run(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--label",
                label,
                "--json",
                "number,title,body,labels,url,updatedAt",
            ]
        )
        try:
            decoded = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise IntakeError(f"unexpected gh issue list output for {repo}") from exc
        for issue in decoded if isinstance(decoded, list) else []:
            labels = [
                str(entry.get("name", ""))
                for entry in issue.get("labels", [])
                if isinstance(entry, dict)
            ]
            body = str(issue.get("body", ""))
            updated_at = str(issue.get("updatedAt", ""))
            items.append(
                IntakeItem(
                    repo=repo,
                    number=int(issue.get("number", 0)),
                    title=str(issue.get("title", "")),
                    url=str(issue.get("url", "")),
                    labels=tuple(labels),
                    updated_at=updated_at,
                    score=score_issue(labels=labels, body=body, updated_at=updated_at),
                    body_complete=_body_complete(body),
                )
            )
    return sorted(items, key=lambda item: item.score, reverse=True)


def find_fingerprint_issue(repo: str, fingerprint: str, *, client: GhClient) -> int | None:
    """Return the open issue number already carrying this signal fingerprint."""
    raw = client.run(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f"{FINGERPRINT_MARKER} {fingerprint}",
            "--json",
            "number",
        ]
    )
    try:
        decoded = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise IntakeError(f"unexpected gh search output for {repo}") from exc
    for issue in decoded if isinstance(decoded, list) else []:
        if isinstance(issue, dict) and issue.get("number") is not None:
            return int(issue["number"])
    return None


def render_candidate_body(
    *,
    context: str,
    action_items: list[str],
    related: list[str],
    fingerprint: str,
) -> str:
    """Self-contained candidate body per the org issue convention."""
    lines = [
        "## Context",
        "",
        context.strip(),
        "",
        "## Action items",
        "",
        *(f"{index}. {item}" for index, item in enumerate(action_items, start=1)),
        "",
        "## Related",
        "",
        *(f"- {item}" for item in related),
        "",
        "Filed by the Engineering Loop intake scan. Review and relabel to",
        f"`{APPROVED_LABEL}` to make it eligible for autonomous runs.",
        "",
        f"<!-- {FINGERPRINT_MARKER} {fingerprint} -->",
    ]
    return "\n".join(lines)


def file_candidate_issue(
    *,
    repo: str,
    title: str,
    context: str,
    action_items: list[str],
    related: list[str],
    fingerprint: str,
    client: GhClient,
) -> str:
    """File one candidate issue. Applies ``loop:candidate`` and nothing else."""
    body = render_candidate_body(
        context=context,
        action_items=action_items,
        related=related,
        fingerprint=fingerprint,
    )
    stdout = client.run(
        [
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--label",
            CANDIDATE_LABEL,
            "--body",
            body,
        ]
    )
    return stdout.strip()


def ensure_labels(repos: list[str], *, client: GhClient) -> list[str]:
    """Create the two protocol labels (explicit operator action, idempotent)."""
    created: list[str] = []
    for repo in repos:
        for label, color, description in (
            (CANDIDATE_LABEL, "fbca04", "Machine-proposed work awaiting human triage"),
            (APPROVED_LABEL, "0e8a16", "Human-approved; eligible for autonomous loop runs"),
        ):
            client.run(
                [
                    "label",
                    "create",
                    label,
                    "--repo",
                    repo,
                    "--color",
                    color,
                    "--description",
                    description,
                    "--force",
                ]
            )
            created.append(f"{repo}:{label}")
    return created


@dataclass
class IntakeReport:
    """Outcome of one intake scan."""

    filed: list[dict[str, Any]] = field(default_factory=list)
    deduplicated: list[dict[str, Any]] = field(default_factory=list)
    skipped_miners: list[str] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "filed": self.filed,
            "deduplicated": self.deduplicated,
            "skipped_miners": self.skipped_miners,
            "dry_run": self.dry_run,
        }
