import contextlib
import importlib.util
import io
import http.client
from email.message import Message
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.response

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
            (200, {**good, "status": []}, 3),
            (200, {**good, "status": {}}, 3),
            (200, {**good, "status": None}, 3),
            (200, {**good, "outbox_worker": {}}, 3),
            (200, {**good, "outbox_worker": {"enabled": True}}, 3),
            (200, {**good, "outbox_worker": {"enabled": 1, "running": True}}, 3),
            (200, {**good, "delivery": {"status": "ok"}}, 3),
            (200, {**good, "delivery": {"status": "ok", "reasons": None}}, 3),
            (200, {**good, "delivery": {"status": "ok", "reasons": [{}]}}, 3),
            (302, {"status": "degraded"}, 3),
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


    def test_protocol_errors_are_sanitized_during_open_and_read(self):
        for error in (http.client.BadStatusLine("private server line"), http.client.IncompleteRead(b"private body")):
            for stage in ("open", "read"):
                with self.subTest(error=type(error).__name__, stage=stage):
                    opener = MagicMock()
                    if stage == "open":
                        opener.open.side_effect = error
                    else:
                        opener.open.return_value.read.side_effect = error
                    output = io.StringIO()
                    with patch.object(check.urllib.request, "build_opener", return_value=opener), contextlib.redirect_stdout(output):
                        self.assertEqual(check.main(["::1", "8000"]), 2)
                    self.assertNotIn("private", output.getvalue())
                    self.assertNotIn("Traceback", output.getvalue())

    def test_redirect_cannot_contact_a_second_target(self):
        targets = []

        class RedirectServer(check.urllib.request.HTTPHandler):
            def http_open(self, request):
                targets.append(request.full_url)
                headers = Message()
                headers["Location"] = "http://unexpected.example/health/cases"
                response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
                response.msg = "Found"
                return response

        build = check.urllib.request.build_opener

        def local_opener(*handlers):
            # Exercise the actual urllib redirect chain without opening sockets.
            return build(*handlers, RedirectServer())

        output = io.StringIO()
        with patch.object(check.urllib.request, "build_opener", side_effect=local_opener), contextlib.redirect_stdout(output):
            self.assertEqual(check.main(["::1", "8000"]), 3)
        self.assertEqual(targets, ["http://[::1]:8000/health/cases"])
        self.assertIn("UNKNOWN", output.getvalue())

    def test_json_value_shapes_never_escape_as_tracebacks(self):
        for value in (None, [], {}, 0, 1, False, "ok"):
            for field in ("status", "delivery", "outbox_worker"):
                payload = {"status": "ok", "delivery": {"status": "ok", "reasons": []},
                           "outbox_worker": {"enabled": True, "running": True}}
                payload[field] = value
                with self.subTest(field=field, value=value):
                    result, _ = check.assess(200, json.dumps(payload))
                    self.assertIn(result, (0, 2, 3))
