"""Phase E (v2): label-gated issue intake and read-only signal miners."""

from __future__ import annotations

import json

import pytest

from hyrule_engineering_loop.intake import (
    APPROVED_LABEL,
    CANDIDATE_LABEL,
    Signal,
    ensure_labels,
    file_candidate_issue,
    list_issues_with_label,
    mine_all_signals,
    signals_to_candidates,
)
from hyrule_engineering_loop.intake.github_issues import IntakeError, signal_fingerprint
from hyrule_engineering_loop.intake.signals import mine_icinga, mine_prometheus

MUTATING_GH_COMMANDS = {"create", "edit", "close", "delete", "comment", "transfer", "label"}


class FakeGh:
    """Records every gh invocation; serves canned JSON per (command, repo)."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.responses = responses or {}

    def run(self, args: list[str]) -> str:
        self.calls.append(list(args))
        key = " ".join(args[:2])
        if key in self.responses:
            return self.responses[key]
        for full, response in self.responses.items():
            if " ".join(args).startswith(full):
                return response
        return "[]"

    def mutating_calls(self) -> list[list[str]]:
        return [
            call
            for call in self.calls
            if any(part in MUTATING_GH_COMMANDS for part in call[:2])
        ]


def _signal(identifier: str = "node-up@dns") -> Signal:
    return Signal(
        source="icinga",
        identifier=identifier,
        title=f"Icinga unhandled problem: {identifier}",
        context="The check keeps failing.",
        action_items=("Investigate.", "Fix or tune."),
        related=("icinga",),
    )


def test_miners_are_read_only_and_dry_run_files_nothing() -> None:
    gh = FakeGh()
    signals, skipped = mine_all_signals(repo="AS215932/network-operations", client=gh)

    # No Icinga/Prometheus env configured: those miners skip gracefully.
    assert any("icinga" in note for note in skipped)
    assert any("prometheus" in note for note in skipped)
    # The only gh calls are read-only `run list` queries.
    assert gh.mutating_calls() == []
    assert all(call[:2] == ["run", "list"] for call in gh.calls)

    report = signals_to_candidates(
        [_signal()], repo="AS215932/network-operations", client=gh, dry_run=True
    )
    assert report.dry_run is True
    assert len(report.filed) == 1
    assert "url" not in report.filed[0]
    assert gh.mutating_calls() == []
    assert signals == []


def test_duplicate_signal_files_nothing() -> None:
    fingerprint = signal_fingerprint("icinga", "node-up@dns")
    gh = FakeGh(
        responses={
            "issue list": json.dumps([{"number": 240}]),
        }
    )

    report = signals_to_candidates(
        [_signal()], repo="AS215932/network-operations", client=gh
    )

    assert report.filed == []
    assert report.deduplicated[0]["existing_issue"] == 240
    assert report.deduplicated[0]["fingerprint"] == fingerprint
    assert gh.mutating_calls() == []


def test_candidate_issue_body_and_label_protocol() -> None:
    gh = FakeGh(
        responses={
            "issue list": "[]",
            "issue create": "https://github.com/AS215932/network-operations/issues/241\n",
        }
    )

    report = signals_to_candidates(
        [_signal()], repo="AS215932/network-operations", client=gh
    )

    assert report.filed[0]["url"].endswith("/issues/241")
    create_calls = [call for call in gh.calls if call[:2] == ["issue", "create"]]
    assert len(create_calls) == 1
    create = create_calls[0]

    # AC3: the candidate label and nothing else. The body may *mention*
    # loop:approved (it instructs the human how to authorize), but the label
    # arguments never apply it.
    label_values = [create[i + 1] for i, part in enumerate(create) if part == "--label"]
    assert label_values == [CANDIDATE_LABEL]
    body_index = create.index("--body") + 1
    args_without_body = [part for i, part in enumerate(create) if i != body_index]
    assert APPROVED_LABEL not in " ".join(args_without_body)

    body = create[body_index]
    assert "## Context" in body
    assert "## Action items" in body
    assert "## Related" in body
    assert signal_fingerprint("icinga", "node-up@dns") in body


def test_nothing_in_intake_can_apply_the_approved_label() -> None:
    import hyrule_engineering_loop.intake.github_issues as gi
    import hyrule_engineering_loop.intake.signals as sig
    import inspect

    for module in (gi, sig):
        source = inspect.getsource(module)
        # The approved label may be referenced (listing, docs) but never
        # passed to a mutating gh subcommand.
        for line in source.splitlines():
            if "issue" in line and "create" in line:
                assert "APPROVED_LABEL" not in line


def test_approved_queue_lists_and_scores() -> None:
    gh = FakeGh(
        responses={
            "issue list": json.dumps(
                [
                    {
                        "number": 1,
                        "title": "routine cleanup",
                        "body": "## Context\nx\n## Action items\n1. y\n## Related\n- z",
                        "labels": [{"name": APPROVED_LABEL}, {"name": "routine"}],
                        "url": "https://example/1",
                        "updatedAt": "2026-06-01T00:00:00Z",
                    },
                    {
                        "number": 2,
                        "title": "critical fix",
                        "body": "no sections",
                        "labels": [{"name": APPROVED_LABEL}, {"name": "critical"}],
                        "url": "https://example/2",
                        "updatedAt": "2026-06-10T00:00:00Z",
                    },
                ]
            )
        }
    )

    items = list_issues_with_label(
        ["AS215932/network-operations"], APPROVED_LABEL, client=gh
    )

    assert [item.number for item in items] == [2, 1]
    assert items[0].score > items[1].score
    assert items[1].body_complete is True
    assert items[0].body_complete is False
    assert gh.mutating_calls() == []


def test_ensure_labels_is_an_explicit_operator_action() -> None:
    gh = FakeGh()
    created = ensure_labels(["AS215932/network-operations"], client=gh)

    assert created == [
        f"AS215932/network-operations:{CANDIDATE_LABEL}",
        f"AS215932/network-operations:{APPROVED_LABEL}",
    ]
    # Label creation goes through `gh label create` only when invoked
    # explicitly — the miners themselves never call it (covered above).
    assert all(call[:2] == ["label", "create"] for call in gh.calls)


def test_icinga_and_prometheus_miners_parse_get_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict[str, str]]] = []

    def fake_get(url: str, headers: dict[str, str]) -> str:
        requests.append((url, headers))
        if "/v1/objects/services" in url:
            return json.dumps(
                {"results": [{"attrs": {"__name": "dns!dns-soa", "state": 2}}]}
            )
        return json.dumps(
            {
                "data": {
                    "alerts": [
                        {
                            "state": "firing",
                            "labels": {"alertname": "NodeDown", "instance": "web"},
                        },
                        {"state": "pending", "labels": {"alertname": "Ignored"}},
                    ]
                }
            }
        )

    monkeypatch.setenv("HYRULE_ICINGA_URL", "https://mon.example:5665")
    monkeypatch.setenv("HYRULE_ICINGA_USER", "loop")
    monkeypatch.setenv("HYRULE_ICINGA_PASSWORD", "x")
    monkeypatch.setenv("HYRULE_PROMETHEUS_URL", "http://mon.example:9090")

    icinga_signals, icinga_note = mine_icinga(fake_get)
    prom_signals, prom_note = mine_prometheus(fake_get)

    assert icinga_note is None and prom_note is None
    assert icinga_signals[0].identifier == "dns!dns-soa"
    assert prom_signals[0].identifier == "NodeDown@web"
    assert len(prom_signals) == 1  # pending alerts are not signals
    assert all(url.startswith(("https://mon", "http://mon")) for url, _ in requests)


def test_gh_failure_raises_intake_error() -> None:
    class FailingGh:
        def run(self, args: list[str]) -> str:
            raise IntakeError("boom")

    with pytest.raises(IntakeError):
        list_issues_with_label(["r"], APPROVED_LABEL, client=FailingGh())


def test_candidate_filing_payload_is_self_contained() -> None:
    gh = FakeGh(responses={"issue create": "https://example/242\n"})
    url = file_candidate_issue(
        repo="AS215932/network-operations",
        title="t",
        context="why this matters",
        action_items=["step one"],
        related=["docs/network-flows.md"],
        fingerprint="abc123",
        client=gh,
    )
    assert url == "https://example/242"
    body = gh.calls[0][gh.calls[0].index("--body") + 1]
    assert "Review and relabel" in body
    assert f"`{APPROVED_LABEL}`" in body
