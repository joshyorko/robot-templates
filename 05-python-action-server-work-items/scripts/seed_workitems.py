#!/usr/bin/env python3
"""Seed input work items using any actions-work-items adapter env file."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from actions import workitems


DEFAULT_INPUT = "devdata/work-items-in/input-for-producer/work-items.json"


def load_env(path: Path) -> None:
    data = json.loads(path.read_text())
    for key, value in data.items():
        if key.startswith("_"):
            continue
        os.environ[key] = os.path.expandvars(str(value))


def load_items(path: Path) -> list[dict]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict):
        return [raw]
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain one work item object or a list")
    return raw


def seed(env_path: Path, input_path: Path) -> int:
    load_env(env_path)
    adapter = workitems.create_adapter()
    workitems.init(adapter)

    queue_name = os.getenv("RC_WORKITEM_QUEUE_NAME", "default")
    created = 0
    for item in load_items(input_path):
        payload = item.get("payload", item)
        files = item.get("files") or None
        item_id = workitems.seed_input(payload=payload, files=files, queue_name=queue_name)
        created += 1
        print(f"seeded {item_id} -> queue={queue_name} payload_keys={list(payload)[:6]}")

    return created


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, help="Adapter env JSON")
    parser.add_argument("--json", default=DEFAULT_INPUT, help="Input work-items JSON")
    args = parser.parse_args(argv)

    count = seed(Path(args.env), Path(args.json))
    print(f"done: seeded {count} item(s)")


if __name__ == "__main__":
    main()
