"""Configuration helpers for this repository.

This module exposes only the adapter configuration helpers used by the
robot tasks. Project-specific parameter loading was removed to keep the
module focused and portable.
"""
from __future__ import annotations

import os
import logging
from typing import Dict

LOGGER = logging.getLogger(__name__)


# T012: Adapter configuration loading
def get_adapter_config() -> Dict[str, object]:
    """Load adapter configuration from environment variables.

    Returns a dictionary with adapter selection and connection settings. The
    function intentionally reads from environment variables (not files) so
    Robocorp tasks can configure adapters via the environment.
    """
    config: Dict[str, object] = {
        # Adapter selection (required)
        "adapter_class": os.getenv("RC_WORKITEM_ADAPTER", ""),

        # Common configuration
        "queue_name": os.getenv("RC_WORKITEM_QUEUE_NAME", "default"),
        "files_dir": os.getenv("RC_WORKITEM_FILES_DIR", "devdata/work_item_files"),
        "orphan_timeout_minutes": int(os.getenv("RC_WORKITEM_ORPHAN_TIMEOUT_MINUTES", "30")),

        # SQLite configuration
        "db_path": os.getenv("RC_WORKITEM_DB_PATH", ""),

        # Redis configuration
        "redis_url": os.getenv("RC_REDIS_URL", "redis://localhost:6379/0"),

        # DocumentDB / MongoDB configuration
        "docdb_uri": os.getenv("DOCDB_URI", ""),
        "docdb_database": os.getenv("DOCDB_DATABASE", ""),
    }

    # Validate required configuration early for clarity
    if not config["adapter_class"]:
        raise ValueError(
            "RC_WORKITEM_ADAPTER environment variable is required. "
            "Example: actions.work_items.SQLiteAdapter"
        )

    return config


def validate_adapter_config(adapter_class: str, config: Dict[str, object]) -> None:
    """Validate adapter configuration for specific adapter type.

    Raises ValueError when required settings for the selected adapter type are
    missing. Keeps checks minimal and focused on what runtime needs.
    """
    acl = adapter_class.lower()

    if "sqlite" in acl:
        if not config.get("db_path"):
            raise ValueError(
                "RC_WORKITEM_DB_PATH environment variable required for SQLite adapter. "
                "Example: devdata/work_items.db"
            )

    elif "redis" in acl:
        if not config.get("redis_url"):
            raise ValueError(
                "RC_REDIS_URL environment variable required for Redis adapter. "
                "Example: redis://localhost:6379/0"
            )

    elif "docdb" in acl or "documentdb" in acl:
        if not config.get("docdb_uri") or not config.get("docdb_database"):
            raise ValueError(
                "DOCDB_URI and DOCDB_DATABASE are required for DocumentDB adapter. "
                "Example: mongodb://qauser:qapassword@localhost:27017/?authSource=admin"
            )


if __name__ == "__main__":
    # Example usage for local debugging: only exercise adapter helpers
    try:
        adapter_config = get_adapter_config()
        print(f"Adapter configuration: {adapter_config}")
    except Exception as e:
        print(f"Adapter configuration error: {e}")
