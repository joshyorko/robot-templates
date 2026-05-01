#!/usr/bin/env python3
"""Smoke-test actions-work-items adapters with the module-shaped API."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from actions import workitems


def run_flow(adapter, input_queue: str, output_queue: str, label: str) -> None:
    workitems.init(adapter)
    workitems.seed_input(payload={"adapter": label, "value": 1}, queue_name=input_queue)

    seen = []
    output_id = None
    for item in workitems.inputs:
        with item:
            workitems.outputs.create(
                {"adapter": label, "processed": True, "source": item.payload}
            )
            seen.append(item.id)
            output_id = workitems.outputs.last.id

    if len(seen) != 1:
        raise AssertionError(f"{label}: expected 1 item, saw {len(seen)}")

    input_stats = adapter.get_queue_stats(input_queue)
    output_stats = adapter.get_queue_stats(output_queue)
    if input_stats["done"] != 1 or output_stats["pending"] != 1:
        raise AssertionError(f"{label}: bad stats input={input_stats} output={output_stats}")

    for item_id in [*seen, output_id]:
        if item_id:
            try:
                adapter.delete_item(item_id)
            except Exception:
                pass

    print(f"PASS {label}: input={input_stats} output={output_stats}")


def smoke_file(root: Path) -> None:
    adapter = workitems.FileAdapter(
        input_path=str(root / "file-in"),
        output_path=str(root / "file-out"),
    )
    run_flow(adapter, "file", "file_output", "file")


def smoke_sqlite(root: Path) -> None:
    adapter = workitems.SQLiteAdapter(
        db_path=str(root / "work_items.db"),
        queue_name="sqlite_in",
        output_queue_name="sqlite_out",
        files_dir=str(root / "sqlite-files"),
    )
    run_flow(adapter, "sqlite_in", "sqlite_out", "sqlite")


def smoke_redis(root: Path) -> None:
    os.environ.setdefault("RC_REDIS_URL", "redis://localhost:6379/0")
    os.environ["RC_WORKITEM_QUEUE_NAME"] = "redis_smoke_in"
    os.environ["RC_WORKITEM_OUTPUT_QUEUE_NAME"] = "redis_smoke_out"
    os.environ["RC_WORKITEM_FILES_DIR"] = str(root / "redis-files")
    adapter = workitems.RedisAdapter()
    run_flow(adapter, "redis_smoke_in", "redis_smoke_out", "redis")


def smoke_docdb(root: Path) -> None:
    os.environ.setdefault(
        "DOCDB_URI",
        "mongodb://qauser:qapassword@localhost:27017/?authSource=admin",
    )
    os.environ.setdefault("DOCDB_DATABASE", "qa_workitems")
    os.environ["RC_WORKITEM_QUEUE_NAME"] = "docdb_smoke_in"
    os.environ["RC_WORKITEM_OUTPUT_QUEUE_NAME"] = "docdb_smoke_out"
    os.environ["RC_WORKITEM_FILES_DIR"] = str(root / "docdb-files")
    adapter = workitems.DocumentDBAdapter()
    run_flow(adapter, "docdb_smoke_in", "docdb_smoke_out", "docdb")


SMOKES = {
    "file": smoke_file,
    "sqlite": smoke_sqlite,
    "redis": smoke_redis,
    "docdb": smoke_docdb,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--adapter",
        action="append",
        choices=sorted(SMOKES),
        default=[],
        help="Adapter to test. Repeat for multiple adapters.",
    )
    args = parser.parse_args()
    adapters = args.adapter or ["file", "sqlite"]

    with tempfile.TemporaryDirectory(prefix="actions-workitems-smoke-") as tmp:
        root = Path(tmp)
        for adapter in adapters:
            SMOKES[adapter](root)


if __name__ == "__main__":
    main()
