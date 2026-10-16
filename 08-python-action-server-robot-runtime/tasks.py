"""RCC task entry points for the reusable invoice_robot workload."""

import os
from pathlib import Path

from robocorp.tasks import task

from invoice_robot import validate_batch


ROOT = Path(__file__).resolve().parent


def _paths() -> tuple[Path, Path]:
    input_value = os.environ.get("INVOICE_INPUT")
    artifacts_value = os.environ.get("ROBOT_ARTIFACTS")
    input_path = Path(input_value) if input_value else ROOT / "devdata/invoices.json"
    output_dir = Path(artifacts_value) if artifacts_value else ROOT / "output"
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    return input_path, output_dir


@task
def validate_batch_task() -> None:
    """Run the deterministic batch and write canonical artifacts."""
    input_path, output_dir = _paths()
    validate_batch(input_path, output_dir)


@task
def fail_batch() -> None:
    """Write failure evidence and exit nonzero for failure-path acceptance."""
    input_path, output_dir = _paths()
    validate_batch(input_path, output_dir, force_failure=True)
