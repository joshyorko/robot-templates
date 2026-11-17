"""Fake-transport protocol checks for the stdlib Actions Runtime client."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from action_server import ActionServerClient, HttpResponse  # noqa: E402


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, bytes | None, dict[str, str], float]] = []

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append((method, url, body, headers, timeout))
        return self.responses.pop(0)


def json_response(value, status=200):
    return HttpResponse(status, {"Content-Type": "application/json"}, json.dumps(value).encode())


class ActionServerClientTests(unittest.TestCase):
    def test_json_request_uses_bearer_auth_and_decodes_response(self):
        transport = FakeTransport([json_response({"robots": []})])
        client = ActionServerClient("http://127.0.0.1:9876/", "test-key", transport=transport)

        self.assertEqual(client.get_json("/api/robots/catalog"), {"robots": []})
        method, url, _body, headers, _timeout = transport.calls[0]
        self.assertEqual((method, url), ("GET", "http://127.0.0.1:9876/api/robots/catalog"))
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    def test_import_builds_multipart_zip_upload(self):
        transport = FakeTransport([json_response({"success": True, "robot_path": "/private/robot"})])
        client = ActionServerClient("http://127.0.0.1:9876", "test-key", transport=transport)
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "robot.zip"
            archive.write_bytes(b"zip-content")
            result = client.import_robot(archive)

        self.assertTrue(result["success"])
        method, url, body, headers, _timeout = transport.calls[0]
        self.assertEqual((method, url), ("POST", "http://127.0.0.1:9876/api/robots/import"))
        self.assertIn(b'name="file"; filename="robot.zip"', body)
        self.assertIn(b"zip-content", body)
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data; boundary="))

    def test_run_body_and_terminal_poll_use_actual_run_route(self):
        transport = FakeTransport(
            [
                json_response({"run_id": "run-test", "status": "REQUESTED"}),
                json_response({"id": "run-test", "status": 2}),
            ]
        )
        client = ActionServerClient("http://127.0.0.1:9876", "test-key", transport=transport)
        submitted = client.run_robot("/private/robot", "ValidateBatch", {"INVOICE_INPUT": "/private/alternate.json"})
        terminal = client.wait_for_run(submitted["run_id"], timeout=1)

        self.assertEqual(terminal["status"], 2)
        method, url, body, _headers, _timeout = transport.calls[0]
        self.assertEqual((method, url), ("POST", "http://127.0.0.1:9876/api/robots/run"))
        self.assertEqual(json.loads(body), {
            "robot_package_path": "/private/robot",
            "task_name": "ValidateBatch",
            "inputs": {"INVOICE_INPUT": "/private/alternate.json"},
        })
        self.assertTrue(transport.calls[1][1].endswith("/api/runs/run-test"))

    def test_collects_persisted_log_and_reports_missing_robot_outputs(self):
        transport = FakeTransport(
            [
                json_response([{"name": "__action_server_output.txt", "size_in_bytes": 7}]),
                HttpResponse(200, {"Content-Type": "text/plain"}, b"task log"),
            ]
        )
        client = ActionServerClient("http://127.0.0.1:9876", "test-key", transport=transport)
        with tempfile.TemporaryDirectory() as temp:
            result = client.collect_artifacts("run-test", Path(temp) / "artifacts")
            self.assertEqual((Path(temp) / "artifacts" / "__action_server_output.txt").read_bytes(), b"task log")

        self.assertEqual(result["log_files"], ["__action_server_output.txt"])
        self.assertEqual(result["required_robot_outputs_missing"], ["report.json", "results.csv"])

    def test_rejects_unsafe_artifact_names(self):
        transport = FakeTransport([json_response([{"name": "../secret", "size_in_bytes": 1}])])
        client = ActionServerClient("http://127.0.0.1:9876", "test-key", transport=transport)

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "unsafe artifact name"):
                client.collect_artifacts("run-test", Path(temp) / "artifacts")


if __name__ == "__main__":
    unittest.main()
