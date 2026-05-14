#!/usr/bin/env python3
"""Inspect output queues used by the producer/consumer/reporter ladder."""

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

# Get all work items in the Consumer's output queue
cursor = conn.execute("""
    SELECT id, queue_name, state, payload
    FROM work_items
    WHERE queue_name IN ('fetch_repos_output', 'fetch_repos_report', 'fetch_repos_done')
       OR queue_name LIKE '%output%'
    ORDER BY created_at DESC
""")

print("Work Items in Output Queues:")
print("-" * 80)

producer_output_count = 0
consumer_output_count = 0
status_counts = {}

for row in cursor:
    payload = json.loads(row['payload'])
    print(f"\nID: {row['id']}")
    print(f"  Queue: {row['queue_name']}")
    print(f"  State: {row['state']}")
    print(f"  Payload keys: {list(payload.keys())}")

    status = payload.get("status")
    if status:
        status_counts[status] = status_counts.get(status, 0) + 1

    if payload.get("URL") and payload.get("Name"):
        producer_output_count += 1
        print("  Format: Producer output repository item")
    elif status and (payload.get("url") or payload.get("name")):
        consumer_output_count += 1
        print(f"  Format: Consumer output item ({status})")
    else:
        print("  Format: Unknown")

print(f"\n{'='*80}")
print("Summary:")
print("-" * 80)
print(f"  Producer output items: {producer_output_count}")
print(f"  Consumer output items: {consumer_output_count}")
print(f"  Consumer statuses: {status_counts}")
print(f"\n{'='*80}")

print("\nExpected queue ladder:")
print("-" * 80)
print("  fetch_repos -> fetch_repos_output -> fetch_repos_report -> fetch_repos_done")

conn.close()
