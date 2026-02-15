from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


class AllowlistError(RuntimeError):
    """Custom exception for allowlist loading issues."""


def load_allowlist(path: Path) -> Dict[str, Any]:
    """Load a JSON allowlist file.

    Args:
        path: Path to the allowlist JSON file.

    Returns:
        Parsed JSON dictionary. Empty dict if file does not exist.
    """
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - configuration error path
        raise AllowlistError(f"Failed to parse allowlist: {path}") from exc

    if not isinstance(data, dict):
        raise AllowlistError(f"Allowlist must be a dictionary: {path}")

    return data
