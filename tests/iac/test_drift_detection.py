import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


class DriftDetectionTest(unittest.TestCase):
    """The nightly drift gate must cover every managed mon monitoring playbook.

    prometheus (rules, #383) and alertmanager (delivery service, #384) are
    applied to mon like any other host; if they are absent from the drift sweep,
    hand-edits on mon (a tweaked rule file, /etc/prometheus/alertmanager.yml,
    /etc/default/prometheus-alertmanager ARGS, service disablement) drift
    silently and never page. See network-operations#386.
    """

    def _playbooks(self):
        text = (REPO / ".github/workflows/drift-detection.yml").read_text()
        m = re.search(r"playbooks=\(([^)]*)\)", text)
        self.assertIsNotNone(m, "could not find the playbooks=(...) array")
        return m.group(1).split()

    def test_monitoring_stack_playbooks_are_in_drift_sweep(self):
        playbooks = self._playbooks()
        for expected in ("prometheus", "alertmanager"):
            self.assertIn(
                expected,
                playbooks,
                f"{expected} playbook must be in the nightly drift-detection sweep",
            )

    def test_existing_drift_targets_are_preserved(self):
        # Guard against an edit dropping the previously-covered playbooks.
        playbooks = self._playbooks()
        for expected in ("firewall", "monitoring", "icinga2", "logs"):
            self.assertIn(expected, playbooks)


if __name__ == "__main__":
    unittest.main()
