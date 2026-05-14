#!/usr/bin/env python3
"""Seed SQLite with initial work items for local producer runs."""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path.
sys.path.insert(0, str(Path(__file__).parent.parent))

from robocorp_adapters_custom._sqlite import SQLiteAdapter


DEFAULT_INPUT = "devdata/work-items-in/input-for-producer/work-items.json"


def load_env(env_json: Path) -> None:
    if env_json.exists():
        data = json.loads(env_json.read_text())
        for key, value in data.items():
            if key.startswith("_"):
                continue
            os.environ[key] = os.path.expandvars(str(value))


def load_items(path: Path) -> list[dict]:
    if not path.exists():
        print(f"Error: Test input file not found: {path}")
        sys.exit(1)

    raw = json.loads(path.read_text())
    if isinstance(raw, dict):
        return [raw]
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain one work item object or a list")
    return raw


def load_files(item: dict) -> list[tuple[str, bytes]] | None:
    files = []
    for name, source in (item.get("files") or {}).items():
        source_path = Path(str(source))
        if source_path.exists():
            files.append((name, source_path.read_bytes()))
    return files or None


def seed_producer_workitems(input_path: Path) -> int:
    os.environ.setdefault("RC_WORKITEM_DB_PATH", "devdata/work_items.db")
    os.environ.setdefault("RC_WORKITEM_FILES_DIR", "devdata/work_item_files")
    os.environ.setdefault("RC_WORKITEM_QUEUE_NAME", "fetch_repos")
    os.environ.setdefault("RC_WORKITEM_OUTPUT_QUEUE_NAME", "fetch_repos_output")

    adapter = SQLiteAdapter()
    work_items = load_items(input_path)
    if not work_items:
        print("Error: No work items found in test input")
        sys.exit(1)

    created = 0
    for item in work_items:
        payload = item.get("payload", item)
        item_id = adapter.seed_input(payload=payload, files=load_files(item))
        created += 1
        print(f"Created producer work item: {item_id}")
        print(f"  Payload: {json.dumps(payload, indent=2)}")

    print(f"\nDatabase: {os.environ['RC_WORKITEM_DB_PATH']}")
    print(f"Input queue: {os.environ['RC_WORKITEM_QUEUE_NAME']}")
    print(f"Output queue: {os.environ.get('RC_WORKITEM_OUTPUT_QUEUE_NAME', '')}")
    print("\nNext:")
    print("  rcc run -t Producer -e devdata/env-sqlite-producer.json")
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="", help="Optional adapter env JSON")
    parser.add_argument("--json", default=DEFAULT_INPUT, help="Input work-items JSON")
    args = parser.parse_args()

    if args.env:
        load_env(Path(args.env))

    count = seed_producer_workitems(Path(args.json))
    print(f"Done. Seeded {count} item(s).")


if __name__ == "__main__":
    main()
