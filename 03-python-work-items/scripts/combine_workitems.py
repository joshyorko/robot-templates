#!/usr/bin/env python3
"""Combine FileAdapter shard outputs into one reporter input directory."""

import argparse
import json
from pathlib import Path


def load_work_items(path: Path) -> list:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        return data.get("workItems", data.get("items", []))
    if isinstance(data, list):
        return data
    raise ValueError(f"{path} must contain a work item list or workItems object")


def combine(source: Path, target: Path) -> int:
    items = []
    for path in sorted(source.glob("shard-*/work-items.json")):
        items.extend(load_work_items(path))

    target.mkdir(parents=True, exist_ok=True)
    (target / "work-items.json").write_text(json.dumps(items, indent=2) + "\n")
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="output/file/consumer-to-reporter")
    parser.add_argument("--target", default="output/file/reporter-input")
    args = parser.parse_args()

    count = combine(Path(args.source), Path(args.target))
    print(f"Combined {count} work item(s) into {args.target}/work-items.json")


if __name__ == "__main__":
    main()
