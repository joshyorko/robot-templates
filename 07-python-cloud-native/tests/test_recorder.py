import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cleanroom.recorder import Recorder, report, start_server, verify_events


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_chain_matches_independent_hash_and_detects_edit(self):
        recorder = Recorder(self.root)
        first = recorder.event("active", environmentDigest="abc")
        recorder.event("observation", exitCode=7)
        events = verify_events(self.root)
        self.assertEqual(events[1]["previousHash"], first["hash"])
        unsigned = {key: value for key, value in first.items() if key != "hash"}
        expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True,
                                  separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(first["hash"], expected)
        path = self.root / "events.jsonl"
        path.write_bytes(path.read_bytes().replace(b'"exitCode":7', b'"exitCode":0'))
        with self.assertRaisesRegex(ValueError, "line 2"):
            verify_events(self.root)

    def test_incomplete_log_cannot_be_extended(self):
        (self.root / "events.jsonl").write_bytes(b'{"partial":true}')
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            Recorder(self.root).event("active")

    def test_reserved_fields_and_nonfinite_values_rejected(self):
        recorder = Recorder(self.root)
        for data in ({"hash": "forged"}, {"value": float("nan")}):
            with self.assertRaises(ValueError):
                recorder.event("active", **data)
        self.assertFalse((self.root / "events.jsonl").exists())

    def test_closure_requires_existing_local_evidence(self):
        recorder = Recorder(self.root)
        for evidence in (None, "missing.json", "../outside.json", "/etc/hosts"):
            with self.assertRaises(ValueError):
                recorder.event("closure", evidence=evidence)
        (self.root / "closure.json").write_text('{"status":"blocked"}')
        event = recorder.event("closure", evidence="closure.json", status="blocked")
        self.assertEqual(event["status"], "blocked")

    def test_concurrent_writers_keep_every_event(self):
        errors = []

        def write(index):
            try:
                for step in range(10):
                    Recorder(self.root).event("observation", writer=index, step=step)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        events = verify_events(self.root)
        self.assertEqual(len(events), 40)
        self.assertEqual(len({(e["writer"], e["step"]) for e in events}), 40)

    def test_offline_report_escapes_embedded_script_and_has_no_fake_events(self):
        attack = '</script><script>alert("x")</script>'
        Recorder(self.root).event("active", note=attack)
        html = report(self.root).read_text()
        self.assertNotIn(attack, html)
        embedded = html.split('id="recorded-events" type="application/json">')[1].split('</script>')[0]
        self.assertEqual(json.loads(embedded)[0]["note"], attack)
        self.assertEqual(len(json.loads(embedded)), 1)
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML", html)


class ServerTests(unittest.TestCase):
    def setUp(self):
        RecorderTests.setUp(self)
        try:
            self.server, self.thread = start_server(self.root)
        except PermissionError as exc:
            self.skipTest(f"Loopback binding prohibited by execution sandbox: {exc}")
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def connection(self):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        self.addCleanup(client.close)
        return client

    def test_json_route_and_traversal_blocked(self):
        event = Recorder(self.root).event("active")
        client = self.connection()
        client.request("GET", "/report.json")
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), [event])
        for path in ("/../events.jsonl", "/%2e%2e/events.jsonl", "/events.jsonl"):
            client.request("GET", path)
            response = client.getresponse()
            self.assertEqual(response.status, 404)
            response.read()

    def test_sse_reconnect_then_live_append_and_shutdown(self):
        recorder = Recorder(self.root)
        recorder.event("active")
        second = recorder.event("observation")
        client = self.connection()
        client.request("GET", "/events", headers={"Last-Event-ID": "1"})
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.fp.readline(), b"id: 2\n")
        self.assertEqual(json.loads(response.fp.readline()[6:]), second)
        third = recorder.event("observation", actual=True)
        while True:
            line = response.fp.readline()
            if line.startswith(b"id:"):
                break
        self.assertEqual(line, b"id: 3\n")
        self.assertEqual(json.loads(response.fp.readline()[6:]), third)

    def test_invalid_cursor_and_corrupt_log_fail_closed(self):
        client = self.connection()
        client.request("GET", "/events?after=-1")
        response = client.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        (self.root / "events.jsonl").write_bytes(b"broken\n")
        client.request("GET", "/report.json")
        response = client.getresponse()
        self.assertEqual(response.status, 409)
        response.read()


if __name__ == "__main__":
    unittest.main()
