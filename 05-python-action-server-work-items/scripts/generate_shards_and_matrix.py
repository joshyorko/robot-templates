import json
import math
import shutil
from pathlib import Path
import sys


PRODUCER_OUTPUT = Path("output/file/producer-to-consumer/work-items.json")
SHARDS_DIR = Path("output/file/shards")


def load_work_items(path):
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        return data.get("workItems", data.get("items", []))
    if isinstance(data, list):
        return data
    raise ValueError(f"{path} must contain a work item list or workItems object")


def write_work_items(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"workItems": items}, indent=2))


def main(max_workers):
    # Read work items from producer output
    work_items = load_work_items(PRODUCER_OUTPUT)
    max_workers = int(max_workers)
    total = len(work_items)

    if total == 0:
        print("No work items to process, creating empty matrix.")
        matrix_config = {'matrix': {'include': []}}
        with open('output/matrix-output.json', 'w') as f:
            json.dump(matrix_config, f)
        print("Generated empty matrix.")
        return

    # Adjust number of workers based on item count
    num_workers = min(max_workers, total)
    per_shard = math.ceil(total / num_workers)

    # Create shards using computed start indices to avoid empty shards
    if SHARDS_DIR.exists():
        shutil.rmtree(SHARDS_DIR)
    SHARDS_DIR.mkdir(parents=True, exist_ok=True)

    shard_starts = list(range(0, total, per_shard))
    for i, start_idx in enumerate(shard_starts):
        shard_items = work_items[start_idx:start_idx + per_shard]
        shard_file = SHARDS_DIR / f"shard-{i}" / "work-items.json"
        write_work_items(shard_file, shard_items)
        print(f'Created shard {i} with {len(shard_items)} items')

    # Build matrix include after shards are created
    matrix_include = [
        {"shard_id": i, "input_path": str(SHARDS_DIR / f"shard-{i}")}
        for i in range(len(shard_starts))
    ]
    # Save matrix config
    matrix_config = {'matrix': {'include': matrix_include}}
    with open('output/matrix-output.json', 'w') as f:
        json.dump(matrix_config, f)
    print(f'Generated matrix with {len(matrix_include)} shards')

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 generate_shards_and_matrix.py <max_workers>")
        sys.exit(1)
    main(sys.argv[1])
