#!/usr/bin/env python3
"""Recover orphaned RESERVED work items in SQLite database."""

import os
from actions import workitems


def main():
    # Load adapter configuration from environment
    db_path = os.getenv("RC_WORKITEM_DB_PATH", "devdata/work_items.db")
    files_dir = os.getenv("RC_WORKITEM_FILES_DIR", "devdata/work_item_files")
    queue_name = os.getenv("RC_WORKITEM_QUEUE_NAME", "fetch_repos_output")

    print(f"\n{'='*80}")
    print(f"Recovering Orphaned Work Items")
    print(f"{'='*80}")
    print(f"Database: {db_path}")
    print(f"Queue: {queue_name}")
    print(f"Files Dir: {files_dir}\n")

    adapter = workitems.SQLiteAdapter(
        db_path=db_path,
        queue_name=queue_name,
        files_dir=files_dir,
    )

    # Recover orphaned items
    recovered = adapter.recover_orphaned_work_items()

    if recovered:
        print(f"\n✅ Recovered {len(recovered)} orphaned work item(s):")
        for item_id in recovered:
            print(f"  - {item_id}")
    else:
        print("\n✅ No orphaned work items found.")

    print(f"\n{'='*80}\n")

if __name__ == "__main__":
    main()
