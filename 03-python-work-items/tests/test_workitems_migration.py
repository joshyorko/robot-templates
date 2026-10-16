"""Offline integration checks for the fetch-repos work item boundaries."""

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import workitems


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class WorkItemsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_sqlite_seeder_preserves_payload_and_attachment_bytes(self):
        from scripts.seed_sqlite_db import seed_producer_workitems

        attachment = self.root / "seed.txt"
        attachment.write_bytes(b"repository seed\x00\xff")
        source = self.root / "seeds.json"
        source.write_text(json.dumps([{
            "payload": {"org": "offline-test"},
            "files": {"seed.txt": str(attachment)},
        }]))
        os.environ.update({
            "RC_WORKITEM_DB_PATH": str(self.root / "items.db"),
            "RC_WORKITEM_FILES_DIR": str(self.root / "files"),
            "RC_WORKITEM_QUEUE_NAME": "fetch_repos",
            "RC_WORKITEM_OUTPUT_QUEUE_NAME": "fetch_repos_output",
        })

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(seed_producer_workitems(source), 1)

        adapter = workitems.create_adapter("SQLiteAdapter")
        item_id = adapter.reserve_input()
        self.assertEqual(adapter.load_payload(item_id), {"org": "offline-test"})
        self.assertEqual(adapter.get_file(item_id, "seed.txt"), b"repository seed\x00\xff")
        adapter.create_output(item_id, {"Name": "example", "URL": "file:///example"})
        adapter.release_input(item_id, workitems.State.DONE)
        self.assertEqual(adapter.get_queue_stats("fetch_repos")["done"], 1)
        self.assertEqual(adapter.get_queue_stats("fetch_repos_output")["pending"], 1)

    def test_file_stage_envs_handoff_list_fixtures(self):
        fixture = [{"payload": {"org": "offline-test"}, "files": {}}]
        source_text = json.dumps(fixture)
        previous_output = None

        for name in ("env-for-producer.json", "env-for-consumer.json", "env-for-reporter.json"):
            config = json.loads((ROOT / "devdata" / name).read_text())
            for key in ("RC_WORKITEM_INPUT_PATH", "RC_WORKITEM_OUTPUT_PATH"):
                config[key] = str(self.root / config[key])
            input_path = Path(config["RC_WORKITEM_INPUT_PATH"])
            output_path = Path(config["RC_WORKITEM_OUTPUT_PATH"])
            if previous_output is None:
                fixture_path = input_path if input_path.suffix == ".json" else input_path / "work-items.json"
                fixture_path.parent.mkdir(parents=True, exist_ok=True)
                fixture_path.write_text(source_text)
            else:
                self.assertEqual(input_path, previous_output)

            os.environ.update(config)
            workitems.init(workitems.create_adapter())
            seen = []
            for item in workitems.inputs:
                with item:
                    seen.append(item.payload)
                    workitems.outputs.create({"org": item.payload["org"]})
            self.assertEqual(seen, [{"org": "offline-test"}])
            outputs = json.loads(output_path.read_text())
            self.assertEqual(outputs[0]["payload"], {"org": "offline-test"})
            previous_output = output_path

        initial_path = self.root / "devdata/work-items-in/input-for-producer/work-items.json"
        self.assertEqual(initial_path.read_text(), source_text)

    def test_sqlite_recovery_makes_abandoned_input_reservable(self):
        from scripts.recover_orphaned_items import main

        os.environ.update({
            "RC_WORKITEM_DB_PATH": str(self.root / "items.db"),
            "RC_WORKITEM_FILES_DIR": str(self.root / "files"),
            "RC_WORKITEM_QUEUE_NAME": "fetch_repos_output",
            "RC_WORKITEM_OUTPUT_QUEUE_NAME": "fetch_repos_report",
        })
        adapter = workitems.create_adapter("SQLiteAdapter")
        item_id = adapter.seed_input({"Name": "abandoned"})
        self.assertEqual(adapter.reserve_input(), item_id)
        with sqlite3.connect(self.root / "items.db") as conn:
            conn.execute("UPDATE work_items SET reserved_at = ? WHERE id = ?", (
                "2000-01-01T00:00:00+00:00", item_id,
            ))
        with contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertEqual(adapter.reserve_input(), item_id)


if __name__ == "__main__":
    unittest.main()
