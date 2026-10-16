import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from redactor.integrity import source_digest
from redactor.mcp import serve
from redactor.corpus import generate


HAS_PDF = importlib.util.find_spec("pypdfium2") is not None and importlib.util.find_spec("PIL") is not None


class ProtocolTest(unittest.TestCase):
    def exchange(self, requests):
        reader = io.StringIO("\n".join(json.dumps(request) for request in requests) + "\n")
        writer = io.StringIO()
        serve(reader, writer, "/corpus", "/output", "sha256:test")
        return [json.loads(line) for line in writer.getvalue().splitlines()]

    def initialize(self, identity=1):
        return {"jsonrpc": "2.0", "id": identity, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}

    def initialized(self):
        return {"jsonrpc": "2.0", "method": "notifications/initialized"}

    def test_lifecycle_and_notification_silence(self):
        responses = self.exchange([self.initialize(), {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {"jsonrpc": "2.0", "id": 3, "method": "ping"}])
        self.assertEqual([response["id"] for response in responses], [1, 2, 3])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(responses[1]["result"]["tools"][0]["name"], "redact_document")
        self.assertEqual(responses[2]["result"], {})

    def test_uninitialized_and_unknown_method(self):
        responses = self.exchange([{"jsonrpc": "2.0", "id": 0, "method": "tools/list"}, self.initialize(), self.initialized(), {"jsonrpc": "2.0", "id": 2, "method": "unknown"}])
        self.assertEqual(responses[0]["error"]["code"], -32000)
        self.assertEqual(responses[-1]["error"]["code"], -32601)

    def test_tool_result_and_invalid_arguments(self):
        result = {"id": "doc-0001", "sha256": "a" * 64}
        valid = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "redact_document", "arguments": {"document": "doc-0001"}}}
        bad = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "redact_document", "arguments": {"document": "doc-0001", "extra": True}}}
        with patch("redactor.mcp.redact_document", return_value=result) as tool:
            responses = self.exchange([self.initialize(), self.initialized(), valid, bad])
            tool.assert_called_once_with("/corpus", "/output", "doc-0001")
        self.assertEqual(json.loads(responses[1]["result"]["content"][0]["text"]), result)
        self.assertFalse(responses[1]["result"]["isError"])
        self.assertEqual(responses[2]["error"]["code"], -32602)

    def test_tools_require_initialized_notification(self):
        tool_list = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        responses = self.exchange([
            self.initialize(), tool_list, self.initialized(),
            dict(tool_list, id=3), self.initialize(4),
        ])
        self.assertEqual(responses[1]["error"]["code"], -32000)
        self.assertIn("tools", responses[2]["result"])
        self.assertEqual(responses[3]["error"]["code"], -32600)

    def test_invalid_id_never_echoed_as_response_id(self):
        for identity in (True, 1.5, {}, []):
            with self.subTest(identity=identity):
                response = self.exchange([{"jsonrpc": "2.0", "id": identity, "method": "ping"}])[0]
                self.assertEqual(response["error"]["code"], -32600)
                self.assertIsNone(response["id"])

    def test_parse_error_recovers(self):
        writer = io.StringIO()
        serve(io.StringIO('{broken\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), writer, "", "", "")
        responses = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["result"], {})

    def test_batches_boolean_ids_and_nonobject_params_rejected(self):
        responses = self.exchange([
            [],
            {"jsonrpc": "2.0", "id": True, "method": "ping"},
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []},
        ])
        self.assertEqual([item["error"]["code"] for item in responses], [-32600, -32600, -32602])

    def test_real_stdio_requires_verified_source(self):
        root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ, PYTHONPATH=str(root), CLEAN_ROOM_CORPUS="/corpus", CLEAN_ROOM_OUTPUT="/output", CLEAN_ROOM_SOURCE_DIGEST="sha256:wrong")
        rejected = subprocess.run([sys.executable, "-m", "redactor.mcp"], input="", text=True, capture_output=True, env=environment, cwd=root)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, "")
        environment["CLEAN_ROOM_SOURCE_DIGEST"] = source_digest(root)
        accepted = subprocess.run([sys.executable, "-m", "redactor.mcp"], input=json.dumps(self.initialize()) + "\n", text=True, capture_output=True, env=environment, cwd=root)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["id"], 1)
        self.assertEqual(accepted.stderr, "")

    @unittest.skipUnless(HAS_PDF, "requires pinned PDFium/Pillow RCC environment")
    def test_real_stdio_renders_tool_and_closes_on_eof(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            corpus, output = Path(folder) / "corpus", Path(folder) / "output"
            expected = generate(corpus, 1)["documents"][0]["expected_text"]
            environment = dict(os.environ, PYTHONPATH=str(root), CLEAN_ROOM_CORPUS=str(corpus), CLEAN_ROOM_OUTPUT=str(output), CLEAN_ROOM_SOURCE_DIGEST=source_digest(root))
            messages = [self.initialize(), {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "redact_document", "arguments": {"document": "doc-0001"}}}]
            process = subprocess.run([sys.executable, "-m", "redactor.mcp"], input="\n".join(json.dumps(message) for message in messages) + "\n", text=True, capture_output=True, env=environment, cwd=root, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stderr, "")
            responses = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual([response["id"] for response in responses], [1, 2])
            self.assertFalse(responses[1]["result"]["isError"])
            evidence = json.loads(responses[1]["result"]["content"][0]["text"])
            self.assertEqual(evidence["text"], expected)
            self.assertTrue((output / evidence["redacted_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
