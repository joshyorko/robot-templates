"""Durable local flight recorder. Hash chains detect edits, not authenticity."""

import fcntl
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _read_events(stream):
    events = []
    previous = "0" * 64
    for line_number, line in enumerate(stream, 1):
        if not line.endswith(b"\n"):
            raise ValueError(f"Incomplete event at line {line_number}")
        try:
            event = json.loads(line)
            unsigned = {key: value for key, value in event.items() if key != "hash"}
            actual = hashlib.sha256(_canonical(unsigned)).hexdigest()
            if (event["seq"] != line_number or event["previousHash"] != previous
                    or event["hash"] != actual
                    or not isinstance(event["kind"], str)
                    or not isinstance(event["timestamp"], str)):
                raise ValueError("Invalid chain or fields")
            timestamp = datetime.fromisoformat(event["timestamp"])
            if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
                raise ValueError("Timestamp must be UTC")
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError(f"Invalid event at line {line_number}: {exc}") from exc
        events.append(event)
        previous = actual
    return events


def verify_events(root):
    """Read a consistent snapshot and reject corrupt or incomplete chains."""
    path = Path(root) / "events.jsonl"
    if not path.exists():
        return []
    with path.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        return _read_events(stream)


class Recorder:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "events.jsonl"

    def event(self, kind, **data):
        """Append one observation, synchronizing concurrent local writers."""
        if not isinstance(kind, str) or not kind:
            raise ValueError("Event kind must be a nonempty string")
        if {"seq", "timestamp", "previousHash", "hash", "kind"} & data.keys():
            raise ValueError("Reserved event field")
        # Round-trip before opening the log, rejecting unsupported/nonfinite values.
        data = json.loads(_canonical(data))
        if kind == "closure":
            evidence = data.get("evidence")
            if not isinstance(evidence, str) or not evidence:
                raise ValueError("Closure requires a relative evidence file")
            path = (self.root / evidence).resolve()
            if (Path(evidence).is_absolute()
                    or not path.is_relative_to(self.root.resolve())
                    or not path.is_file()):
                raise ValueError("Closure evidence must exist inside the recorder root")
        with self.path.open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            events = _read_events(stream)
            event = dict(data, seq=len(events) + 1, kind=kind,
                         timestamp=datetime.now(timezone.utc).isoformat(),
                         previousHash=events[-1]["hash"] if events else "0" * 64)
            event["hash"] = hashlib.sha256(_canonical(event)).hexdigest()
            stream.seek(0, os.SEEK_END)
            stream.write(_canonical(event) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event


def _html(events=None):
    html = Path(__file__).with_name("ui.html").read_text(encoding="utf-8")
    if events is not None:
        # Script elements terminate on literal </script>, including JSON strings.
        payload = _canonical(events).decode("utf-8").replace("<", "\\u003c")
        payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        html = html.replace("<!-- recorded-events -->",
                            '<script id="recorded-events" type="application/json">'
                            + payload + "</script>")
    return html.encode("utf-8")


def report(root):
    """Write an offline HTML snapshot from independently chain-checked events."""
    root = Path(root)
    output = root / "report.html"
    output.write_bytes(_html(verify_events(root)))
    return output


def start_server(root, port=0):
    """Serve only fixed read-only routes on loopback; caller owns shutdown."""
    root = Path(root)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, content, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            route = urlsplit(self.path)
            if route.path not in ("/", "/events", "/report.json"):
                self._send(b"Not found\n", "text/plain", 404)
                return
            try:
                events = verify_events(root)
            except ValueError:
                self._send(b"Recorder integrity check failed\n", "text/plain", 409)
                return
            if route.path == "/":
                self._send(_html(), "text/html; charset=utf-8")
            elif route.path == "/report.json":
                self._send(_canonical(events), "application/json")
            else:
                cursor = self.headers.get("Last-Event-ID")
                if cursor is None:
                    cursor = parse_qs(route.query).get("after", ["0"])[0]
                try:
                    cursor = int(cursor)
                    if cursor < 0:
                        raise ValueError()
                except ValueError:
                    self._send(b"Invalid event cursor\n", "text/plain", 400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.close_connection = True
                self.connection.settimeout(2)
                deadline = time.monotonic() + 15
                try:
                    while not self.server.stopping.is_set() and time.monotonic() < deadline:
                        for event in events:
                            if event["seq"] > cursor:
                                self.wfile.write(b"id: " + str(event["seq"]).encode()
                                                 + b"\ndata: " + _canonical(event) + b"\n\n")
                                cursor = event["seq"]
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        if self.server.stopping.wait(0.5):
                            break
                        events = verify_events(root)
                except (BrokenPipeError, ConnectionResetError, TimeoutError, ValueError):
                    return

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def __init__(self):
            self.stopping = threading.Event()
            super().__init__(("127.0.0.1", port), Handler)

        def shutdown(self):
            self.stopping.set()
            super().shutdown()

        def server_close(self):
            self.stopping.set()
            super().server_close()

    server = Server()
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="cleanroom-recorder")
    thread.start()
    return server, thread
