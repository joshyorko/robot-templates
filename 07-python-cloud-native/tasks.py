"""Deterministic source-task receipt, separate from the environment artifact."""

import json
import os
import platform
import sys
from pathlib import Path

from robocorp.tasks import task


@task
def cloud_native_smoke() -> None:
    """Produce a local receipt without external services or credentials."""
    output_dir = Path(os.environ.get("ROBOT_ARTIFACTS", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "contract": "robot-templates/cloud-native-v1",
        "status": "ok",
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": sys.platform,
        },
    }
    path = output_dir / "cloud-native-smoke.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
