#!/usr/bin/env python3
"""Compatibility wrapper for Redis queue seeding."""

from seed_workitems import main


if __name__ == "__main__":
    main(["--env", "devdata/env-redis-producer.json"])
