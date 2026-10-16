"""Authenticated TLS edge for a loopback-only RCC artifact provider."""

import argparse
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import ssl


MAX_BODY = 256 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
TIMEOUT = 30
_OBJECT = re.compile(r"/v1/objects/sha256/[0-9a-f]{64}\Z")
_MANIFEST = re.compile(r"/v1/manifests/sha256/[0-9a-f]{64}\Z")
_METADATA = frozenset(("/v1/health", "/v1/capabilities", "/v1/protocol"))


def policy(method, path, publish=False):
    """Permit only literal provider paths; deployment trust never uses this edge."""
    if not isinstance(path, str) or len(path) > 256:
        return False
    if method in ("GET", "HEAD"):
        return path in _METADATA or bool(_OBJECT.fullmatch(path) or _MANIFEST.fullmatch(path))
    return bool(publish and method == "PUT" and _OBJECT.fullmatch(path))


def handler(upstream_port, token, publish=False):
    """Return an edge handler with a fixed loopback upstream and secret."""
    if not 1 <= upstream_port <= 65535:
        raise ValueError("invalid upstream port")
    if not token or any(character.isspace() for character in token):
        raise ValueError("token must be nonempty and contain no whitespace")
    authorization = ("Bearer " + token).encode("utf-8")

    class EdgeHandler(BaseHTTPRequestHandler):
        server_version = "CleanRoomEdge"
        sys_version = ""

        def log_message(self, *_args):
            # No request headers, paths, credentials, or body enter logs.
            pass

        def _failure(self, status, message):
            self.close_connection = True
            data = (message + "\n").encode("ascii")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            if status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _proxy(self):
            self.close_connection = True
            auth = self.headers.get_all("Authorization", [])
            if len(auth) != 1 or not hmac.compare_digest(auth[0].encode("utf-8"), authorization):
                self._failure(401, "authentication required")
                return
            if not policy(self.command, self.path, publish):
                self._failure(403, "provider operation forbidden")
                return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get_all("Transfer-Encoding") or len(lengths) > 1:
                self._failure(400, "ambiguous body framing")
                return
            if lengths and (len(lengths[0]) > 10 or not re.fullmatch(r"[0-9]+", lengths[0])):
                self._failure(400, "invalid Content-Length")
                return
            length = int(lengths[0]) if lengths else 0
            if length > MAX_BODY:
                self._failure(413, "request body too large")
                return
            if self.command in ("GET", "HEAD") and length:
                self._failure(400, "read requests must not contain a body")
                return
            if self.command == "PUT" and not lengths:
                self._failure(411, "Content-Length required")
                return
            upstream = http.client.HTTPConnection("127.0.0.1", upstream_port, timeout=TIMEOUT)
            response_started = False
            try:
                self.connection.settimeout(TIMEOUT)
                # RCC implements GET, not HEAD. Suppress the response body locally.
                method = "GET" if self.command == "HEAD" else self.command
                upstream.putrequest(method, self.path, skip_accept_encoding=True)
                request_headers = (
                    "Content-Type", "X-RCC-Artifact-Versions", "X-RCC-Artifact-Extensions", "Range",
                )
                for name in request_headers:
                    values = self.headers.get_all(name, [])
                    if len(values) == 1:
                        upstream.putheader(name, values[0])
                upstream.putheader("Content-Length", str(length))
                upstream.putheader("Connection", "close")
                upstream.endheaders()
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        self._failure(400, "truncated request body")
                        return
                    upstream.send(chunk)
                    remaining -= len(chunk)
                response = upstream.getresponse()
                self.send_response(response.status)
                response_started = True
                response_headers = (
                    "Content-Type", "Content-Length", "ETag", "Cache-Control", "Retry-After",
                    "Accept-Ranges",
                )
                for name in response_headers:
                    value = response.getheader(name)
                    if value is not None:
                        self.send_header(name, value)
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    while chunk := response.read(CHUNK_SIZE):
                        self.wfile.write(chunk)
            except (OSError, http.client.HTTPException, ValueError):
                if not response_started:
                    self._failure(502, "loopback provider unavailable")
            finally:
                upstream.close()

        do_GET = _proxy
        do_HEAD = _proxy
        do_PUT = _proxy
        do_POST = _proxy
        do_DELETE = _proxy
        do_PATCH = _proxy
        do_OPTIONS = _proxy
        do_CONNECT = _proxy
        do_TRACE = _proxy

    return EdgeHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--token-file", type=Path, default=Path("/secrets/token"))
    parser.add_argument("--publish", action="store_true", help="lab spike only: permit object PUT")
    parser.add_argument("--listen-port", type=int, default=8443)
    parser.add_argument("--upstream-port", type=int, default=8123)
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    edge_handler = handler(args.upstream_port, token, args.publish)
    with ThreadingHTTPServer(("0.0.0.0", args.listen_port), edge_handler) as server:
        server.daemon_threads = True
        server.socket = context.wrap_socket(server.socket, server_side=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
