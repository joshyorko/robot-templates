"""Edge policy and framing tests, plus a real loopback TLS test when allowed."""

from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import io
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from cleanroom.edge import MAX_BODY, handler, policy


TOKEN = "test-only-ephemeral-token"
OBJECT = "/v1/objects/sha256/" + "a" * 64
MANIFEST = "/v1/manifests/sha256/" + "a" * 64


def request(method="GET", path=OBJECT, headers=None, body=b"", publish=False, auth=True):
    handler_class = handler(8123, TOKEN, publish)
    instance = handler_class.__new__(handler_class)
    instance.command = method
    instance.path = path
    instance.headers = Message()
    if auth:
        instance.headers["Authorization"] = "Bearer " + TOKEN
    for name, value in headers or []:
        instance.headers[name] = value
    instance.rfile = io.BytesIO(body)
    instance.wfile = io.BytesIO()
    instance.connection = unittest.mock.Mock()
    instance.requestline = method + " " + path + " HTTP/1.1"
    instance.request_version = "HTTP/1.1"
    instance.client_address = ("127.0.0.1", 1)
    return instance


class PolicyTests(unittest.TestCase):
    def test_known_reads_and_head(self):
        for path in (OBJECT, MANIFEST, "/v1/health", "/v1/capabilities", "/v1/protocol"):
            for method in ("GET", "HEAD"):
                with self.subTest(method=method, path=path):
                    self.assertTrue(policy(method, path))

    def test_unknown_and_nonliteral_paths_fail_closed(self):
        for path in ("/", "/v1/admin/audit", "/v1/objects/missing", OBJECT + "?x=1",
                     OBJECT + "#x", OBJECT + "/", OBJECT + "/../health", "/v1/%68ealth",
                     "http://example.test/v1/health", "//v1/health", OBJECT.replace("a", "A"),
                     "/sha256:" + "a" * 64 + "/provenance.json", "/v1/health\n"):
            with self.subTest(path=path):
                self.assertFalse(policy("GET", path))

    def test_writes_default_denied(self):
        for method in ("PUT", "POST", "PATCH", "DELETE", "CONNECT", "TRACE"):
            self.assertFalse(policy(method, OBJECT))

    def test_publisher_only_accepts_object_put(self):
        self.assertTrue(policy("PUT", OBJECT, publish=True))
        for method, path in (("PUT", MANIFEST), ("POST", MANIFEST + "/commit"),
                             ("PUT", "/v1/admin/restore"), ("PUT", "/sha256:a/signatures.json"),
                             ("POST", "/v1/objects/missing")):
            with self.subTest(method=method, path=path):
                self.assertFalse(policy(method, path, publish=True))


class ForwardingTests(unittest.TestCase):
    def invoke(self, instance):
        with patch("cleanroom.edge.http.client.HTTPConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 201
            response.getheader.return_value = None
            response.read.side_effect = [b"created", b""]
            instance._proxy()
            return connection

    def test_content_length_streamed_exactly_without_auth_upstream(self):
        body = b"synthetic-data-" * 9000
        instance = request("PUT", headers=[("Content-Length", str(len(body))),
                                          ("Content-Type", "application/octet-stream")],
                           body=body, publish=True)
        connection = self.invoke(instance)
        connection.assert_called_once_with("127.0.0.1", 8123, timeout=30)
        upstream = connection.return_value
        upstream.putheader.assert_any_call("Content-Length", str(len(body)))
        self.assertEqual(b"".join(call.args[0] for call in upstream.send.call_args_list), body)
        self.assertFalse(any(
            call.args[0].lower() == "authorization"
            for call in upstream.putheader.call_args_list
        ))
        self.assertIn(b"201", instance.wfile.getvalue().splitlines()[0])
        self.assertTrue(instance.wfile.getvalue().endswith(b"created"))
        upstream.close.assert_called_once()

    def test_head_uses_get_and_suppresses_body(self):
        instance = request("HEAD")
        connection = self.invoke(instance)
        connection.return_value.putrequest.assert_called_once_with(
            "GET", OBJECT, skip_accept_encoding=True,
        )
        connection.return_value.getresponse.return_value.read.assert_not_called()
        self.assertNotIn(b"created", instance.wfile.getvalue())

    def test_failures_do_not_contact_upstream(self):
        cases = [
            (request(auth=False), 401),
            (request(headers=[("Authorization", "Bearer second")]), 401),
            (request(headers=[("Transfer-Encoding", "chunked")]), 400),
            (request(headers=[("Content-Length", "0"), ("Content-Length", "0")]), 400),
            (request(headers=[("Content-Length", "1")]), 400),
            (request(headers=[("Content-Length", "-1")]), 400),
            (request(headers=[("Content-Length", "0,0")]), 400),
            (request(headers=[("Content-Length", "9" * 5000)]), 400),
            (request("PUT", publish=True), 411),
            (request("PUT", headers=[("Content-Length", str(MAX_BODY + 1))], publish=True), 413),
            (request("PUT", headers=[("Content-Length", "0")]), 403),
            (request(path="/v1/admin/audit"), 403),
        ]
        for instance, status in cases:
            with self.subTest(status=status, path=instance.path):
                connection = self.invoke(instance)
                connection.assert_not_called()
                self.assertIn(str(status).encode(), instance.wfile.getvalue().splitlines()[0])
                self.assertNotIn(TOKEN.encode(), instance.wfile.getvalue())

    def test_truncated_body_is_rejected(self):
        instance = request("PUT", headers=[("Content-Length", "5")], body=b"no", publish=True)
        connection = self.invoke(instance)
        connection.return_value.getresponse.assert_not_called()
        self.assertIn(b"400", instance.wfile.getvalue().splitlines()[0])

    def test_backend_error_is_sanitized(self):
        instance = request()
        with patch("cleanroom.edge.http.client.HTTPConnection") as connection:
            connection.return_value.endheaders.side_effect = OSError(TOKEN)
            instance._proxy()
        self.assertIn(b"502", instance.wfile.getvalue().splitlines()[0])
        self.assertNotIn(TOKEN.encode(), instance.wfile.getvalue())


class TLSLoopbackTests(unittest.TestCase):
    def test_authenticated_tls_put_with_exact_content_length(self):
        captured = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_PUT(self):
                captured.append((self.path, self.headers.get("Content-Length"),
                                 self.headers.get("Transfer-Encoding"),
                                 self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(201)
                self.send_header("Content-Length", "0")
                self.end_headers()

        try:
            upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        except PermissionError as error:
            self.skipTest("local socket binding prohibited: " + str(error))
        self.addCleanup(upstream.server_close)
        if not shutil.which("openssl"):
            self.fail("openssl prerequisite unavailable for ephemeral TLS certificate")
        with tempfile.TemporaryDirectory() as directory:
            cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", str(key), "-out", str(cert), "-days", "1",
                            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            edge = ThreadingHTTPServer(
                ("127.0.0.1", 0), handler(upstream.server_port, TOKEN, True),
            )
            self.addCleanup(edge.server_close)
            edge.socket = context.wrap_socket(edge.socket, server_side=True)
            for server in (upstream, edge):
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.addCleanup(server.shutdown)
            client_context = ssl.create_default_context(cafile=str(cert))
            client = http.client.HTTPSConnection("localhost", edge.server_port, context=client_context, timeout=5)
            self.addCleanup(client.close)
            body = b"synthetic lab object"
            client.request("PUT", OBJECT, body=body, headers={"Authorization": "Bearer " + TOKEN,
                           "Content-Type": "application/octet-stream"})
            self.assertEqual(client.getresponse().status, 201)
            self.assertEqual(captured, [(OBJECT, str(len(body)), None, body)])


if __name__ == "__main__":
    unittest.main()
