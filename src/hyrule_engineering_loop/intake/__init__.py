"""Intake — the loop's heartbeat (v2 Phase E).

The triage inbox is the GitHub issue tracker itself, gated by labels:
``loop:candidate`` is machine-proposed work awaiting human triage;
``loop:approved`` is human-blessed work eligible for autonomous runs.
Signal miners are read-only and emit candidate issues — never direct runs —
and nothing in this package can apply ``loop:approved``.
"""

from hyrule_engineering_loop.intake.github_issues import (
    APPROVED_LABEL,
    CANDIDATE_LABEL,
    GhCli,
    GhClient,
    IntakeItem,
    ensure_labels,
    file_candidate_issue,
    find_fingerprint_issue,
    list_issues_with_label,
    score_issue,
)
from hyrule_engineering_loop.intake.signals import (
    Signal,
    mine_all_signals,
    signals_to_candidates,
)

__all__ = [
    "APPROVED_LABEL",
    "CANDIDATE_LABEL",
    "GhCli",
    "GhClient",
    "IntakeItem",
    "Signal",
    "ensure_labels",
    "file_candidate_issue",
    "find_fingerprint_issue",
    "list_issues_with_label",
    "mine_all_signals",
    "score_issue",
    "signals_to_candidates",
]
