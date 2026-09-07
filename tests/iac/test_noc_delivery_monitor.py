import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "configs/mon/icinga2/scripts/check_noc_agent_delivery_health.py"
spec = importlib.util.spec_from_file_location("delivery_check", PLUGIN)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class DeliveryMonitorTests(unittest.TestCase):
    def test_status_contract_and_sanitization(self):
        good = {"status": "ok", "outbox_worker": {"enabled": True, "running": True},
                "delivery": {"status": "ok", "reasons": []}}
        cases = [
            (200, good, 0),
            (200, {"status": "ok"}, 3),
            (200, {"status": "disabled"}, 2),
            (503, {"status": "degraded", "error": "private credential text"}, 2),
            (503, {**good, "delivery": {"status": "degraded", "reasons": ["worker_stale", "private credential text"]}}, 2),
            (200, {**good, "outbox_worker": {"enabled": False, "running": False}}, 2),
            (200, {**good, "delivery": {"status": "ok", "reasons": ["reports_overdue"]}}, 2),
            (403, good, 3),
            (200, [], 3),
            (200, {**good, "delivery": []}, 3),
        ]
        for code, payload, expected in cases:
            with self.subTest(code=code, payload=payload):
                result, message = check.assess(code, json.dumps(payload))
                self.assertEqual(result, expected)
                self.assertNotIn("private credential text", message)
        self.assertEqual(check.assess(503, "not JSON")[0], 2)
        self.assertEqual(check.assess(200, "not JSON")[0], 3)

    def test_transport_failure_never_prints_raw_exception(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("private credential text")
        output = io.StringIO()
        with patch.object(check.urllib.request, "build_opener", return_value=opener), contextlib.redirect_stdout(output):
            self.assertEqual(check.main(["::1", "8000"]), 2)
        self.assertNotIn("private credential text", output.getvalue())
        opener.open.assert_called_once_with("http://[::1]:8000/health/cases", timeout=8)

    def test_response_read_is_bounded(self):
        response = MagicMock()
        response.read.return_value = b"x" * 65537
        response.code = 200
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(check.urllib.request, "build_opener", return_value=opener), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check.main(["::1", "8000"]), 3)
        response.read.assert_called_once_with(65537)

    def test_invalid_target_rejected_before_request(self):
        for args in ([], ["example.com", "8000"], ["::1", "0"], ["::1", "65536"], ["::1", "x"]):
            with self.subTest(args=args), patch.object(check.urllib.request, "build_opener") as build:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(check.main(args), 3)
                build.assert_not_called()
