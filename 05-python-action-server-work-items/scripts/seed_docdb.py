#!/usr/bin/env python3
"""Compatibility wrapper for DocumentDB/MongoDB queue seeding."""

from seed_workitems import main


if __name__ == "__main__":
    main(["--env", "devdata/env-docdb-local-producer.json"])
