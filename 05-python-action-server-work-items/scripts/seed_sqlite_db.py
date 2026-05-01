#!/usr/bin/env python3
"""Compatibility wrapper for SQLite queue seeding."""

from seed_workitems import main


if __name__ == "__main__":
    main(["--env", "devdata/env-sqlite-producer.json"])
