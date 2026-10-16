"""Opt-in Redis and MongoDB checks using isolated queues and temporary files."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from actions import workitems


class ServiceAdapterTests(unittest.TestCase):
    def exercise_backend(self, adapter_name, connection_env):
        prefix = f"template_test_{uuid.uuid4().hex}"
        input_queue = f"{prefix}_input"
        output_queue = f"{prefix}_output"
        item_ids = []
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            **connection_env,
            "RC_WORKITEM_ADAPTER": adapter_name,
            "RC_WORKITEM_QUEUE_NAME": input_queue,
            "RC_WORKITEM_OUTPUT_QUEUE_NAME": output_queue,
            "RC_WORKITEM_FILES_DIR": str(Path(tmp) / "files"),
        }):
            adapter = workitems.create_adapter()
            adapters = [adapter]
            try:
                workitems.init(adapter)
                item_ids.append(workitems.seed_input(
                    payload={"org": "offline-test"},
                    files={"input.bin": b"seed\x00\xff"},
                ))
                attachment = Path(tmp) / "result.txt"
                attachment.write_bytes(b"processed\x00\xff")
                seen = []
                for item in workitems.inputs:
                    with item:
                        seen.append(item.payload)
                        self.assertEqual(adapter.get_file(item.id, "input.bin"), b"seed\x00\xff")
                        output = workitems.outputs.create(
                            {"org": item.payload["org"], "status": "success"},
                            files={"result.txt": attachment},
                        )
                        item_ids.append(output.id)
                self.assertEqual(seen, [{"org": "offline-test"}])
                self.assertEqual(adapter.get_queue_stats(input_queue)["done"], 1)
                self.assertEqual(adapter.get_queue_stats(output_queue)["pending"], 1)

                os.environ["RC_WORKITEM_QUEUE_NAME"] = output_queue
                os.environ["RC_WORKITEM_OUTPUT_QUEUE_NAME"] = f"{prefix}_done"
                reader = workitems.create_adapter()
                adapters.append(reader)
                output_id = reader.reserve_input()
                self.assertEqual(reader.load_payload(output_id), {
                    "org": "offline-test", "status": "success",
                })
                self.assertEqual(reader.get_file(output_id, "result.txt"), b"processed\x00\xff")
                reader.release_input(output_id, workitems.State.DONE)
                self.assertEqual(reader.get_queue_stats(output_queue)["done"], 1)

                workitems.init(adapter)
                item_ids.append(workitems.seed_input({"invalid": True}))
                for item in workitems.inputs:
                    item.fail("BUSINESS", code="INVALID", message="test failure path")
                self.assertEqual(adapter.get_queue_stats(input_queue)["failed"], 1)
                self.assertEqual(adapter.get_queue_stats(input_queue)["in_progress"], 0)
            finally:
                try:
                    for item_id in reversed(item_ids):
                        adapter.delete_item(item_id)
                finally:
                    for instance in adapters:
                        instance._client.close()

    @unittest.skipUnless(os.getenv("WORKITEMS_TEST_REDIS_URL"), "Set WORKITEMS_TEST_REDIS_URL")
    def test_redis_payloads_attachments_and_release(self):
        self.exercise_backend("actions.work_items.RedisAdapter", {
            "RC_REDIS_URL": os.environ["WORKITEMS_TEST_REDIS_URL"],
        })

    @unittest.skipUnless(os.getenv("WORKITEMS_TEST_MONGODB_URI"), "Set WORKITEMS_TEST_MONGODB_URI")
    def test_mongodb_payloads_attachments_and_release(self):
        self.exercise_backend("actions.work_items.DocumentDBAdapter", {
            "DOCDB_URI": os.environ["WORKITEMS_TEST_MONGODB_URI"],
            "DOCDB_DATABASE": "robot_template_acceptance",
        })


if __name__ == "__main__":
    unittest.main()
