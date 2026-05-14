#!/usr/bin/env python3
"""Bash script replacement for loading shard work items."""
import os
import json
import sys
from pathlib import Path

def load_shard():
    """Load work items from specific shard file."""
    shard_id = os.getenv("SHARD_ID", "0")

    # Read shard file
    shard_file = Path(f"output/shards/work-items-shard-{shard_id}.json")
    if not shard_file.exists():
        print(f"Shard file not found: {shard_file}")
        sys.exit(1)

    with open(shard_file, 'r') as f:
        shard_data = json.load(f)

    # Create FileAdapter input directory.
    output_dir = Path(f"output/file/shards/shard-{shard_id}")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "work-items.json"
    with open(output_file, 'w') as f:
        json.dump(shard_data, f)

    print(f"Loaded {len(shard_data)} items for shard {shard_id} into {output_dir}")

if __name__ == "__main__":
    load_shard()
