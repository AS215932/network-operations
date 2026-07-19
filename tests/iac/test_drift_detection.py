import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


class DriftDetectionTest(unittest.TestCase):
    """The drift sweep must cover every managed playbook.

    prometheus (rules, #383) and alertmanager (delivery service, #384) are
    applied to mon like any other host; if they are absent from the drift sweep,
    hand-edits on mon (a tweaked rule file, /etc/prometheus/alertmanager.yml,
    /etc/default/prometheus-alertmanager ARGS, service disablement) drift
    silently and never page. See network-operations#386.

    The sweep itself lives in scripts/ci/check-drift.sh, shared by the nightly
    drift-detection workflow and the post-merge apply workflow (#404) so the
    two cannot diverge on scope or semantics.
    """

    def _playbooks(self):
        text = (REPO / "scripts/ci/check-drift.sh").read_text()
        m = re.search(r"default_playbooks=\(([^)]*)\)", text)
        self.assertIsNotNone(m, "could not find the default_playbooks=(...) array in check-drift.sh")
        return m.group(1).split()

    def test_monitoring_stack_playbooks_are_in_drift_sweep(self):
        playbooks = self._playbooks()
        for expected in ("prometheus", "alertmanager"):
            self.assertIn(
                expected,
                playbooks,
                f"{expected} playbook must be in the drift sweep",
            )

    def test_existing_drift_targets_are_preserved(self):
        # Guard against an edit dropping any previously-covered playbook — the
        # full set the sweep ran before prometheus/alertmanager were added, so a
        # regression on ci / rtr_routing / networkd_resolved is caught too.
        # extmon joined via #405.
        playbooks = self._playbooks()
        for expected in (
            "firewall",
            "monitoring",
            "logs",
            "icinga2",
            "ci",
            "rtr_routing",
            "networkd_resolved",
            "extmon",
        ):
            self.assertIn(expected, playbooks)

    def test_both_drift_workflows_use_the_shared_sweep(self):
        # If either workflow reimplements the loop inline, the playbook lists
        # can silently diverge — both must call the shared script.
        for name in ("drift-detection.yml", "post-merge-apply.yml"):
            text = (REPO / ".github/workflows" / name).read_text()
            self.assertIn("check-drift.sh", text, name)
            self.assertNotIn(
                "playbooks=(",
                text,
                f"{name} must not carry its own playbook list — it lives in check-drift.sh",
            )

    def test_unprovisioned_staged_hosts_are_excluded(self):
        text = (REPO / "scripts/ci/check-drift.sh").read_text()
        self.assertIn('CHECK_DRIFT_LIMIT:-all:!ci-pr:!staged', text)

    def test_post_merge_apply_never_auto_applies_the_ci_playbook(self):
        # Applying the runner's own playbook from the runner can restart the
        # runner service mid-job; drift-apply-plan.sh must keep skipping it.
        text = (REPO / "scripts/ci/drift-apply-plan.sh").read_text()
        self.assertIn('pb == "ci"', text)


if __name__ == "__main__":
    unittest.main()
