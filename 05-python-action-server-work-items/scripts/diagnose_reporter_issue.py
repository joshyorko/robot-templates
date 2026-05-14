#!/usr/bin/env python3
"""Inspect SQLite queue shapes used by the producer-consumer-reporter flow."""

import json
import sqlite3
import sys
from pathlib import Path

db_path = sys.argv[1] if len(sys.argv) > 1 else "devdata/work_items.db"

if not Path(db_path).exists():
    print(f"Database not found: {db_path}")
    sys.exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print(f"\n{'='*80}")
print("Reporter Queue Diagnosis")
print(f"{'='*80}\n")

cursor = conn.execute("""
    SELECT id, queue_name, state, payload
    FROM work_items
    WHERE queue_name IN ('fetch_repos_output', 'fetch_repos_report')
    ORDER BY created_at DESC
""")

print("Work Items in Producer/Consumer Output Queues:")
print("-" * 80)

producer_format_count = 0
consumer_format_count = 0
unknown_count = 0

for row in cursor:
    payload = json.loads(row['payload'])
    print(f"\nID: {row['id']}")
    print(f"  Queue: {row['queue_name']}")
    print(f"  State: {row['state']}")
    print(f"  Payload keys: {list(payload.keys())}")

    if payload.get("Name") and payload.get("URL"):
        producer_format_count += 1
        print("  Format: Producer output (repository to clone)")
    elif payload.get("name") and payload.get("status"):
        consumer_format_count += 1
        print("  Format: Consumer output (repository result)")
    else:
        unknown_count += 1
        print("  Format: Unknown")

print(f"\n{'='*80}")
print("Summary:")
print("-" * 80)
print(f"  Producer output items: {producer_format_count}")
print(f"  Consumer output items: {consumer_format_count}")
print(f"  Unknown items: {unknown_count}")
print(f"\n{'='*80}")

print("\nDiagnosis:")
print("-" * 80)
if producer_format_count > 0 and consumer_format_count == 0:
    print("Producer has output items, but Consumer has not created reporter items yet.")
elif consumer_format_count > 0:
    print("Reporter input queue has consumer result items.")
else:
    print("No producer/consumer output items found in the expected queues.")

conn.close()
