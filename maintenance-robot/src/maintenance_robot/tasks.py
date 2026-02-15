from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict

from robocorp.tasks import get_current_task, get_output_dir, task

from maintenance_robot.allowlist_loader import load_allowlist
from maintenance_robot.downloads import DownloadsUpdater
from maintenance_robot.reporter import MaintenanceReport

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

PACKAGE_DIR = Path(__file__).resolve().parent
ROBOT_ROOT = Path(os.getenv("ROBOT_ROOT", str(PACKAGE_DIR.parent.parent))).resolve()


def _resolve_repo_root() -> Path:
    for candidate in [ROBOT_ROOT, *ROBOT_ROOT.parents]:
        if (candidate / ".git").exists():
            return candidate
    return ROBOT_ROOT.parent


REPO_ROOT = _resolve_repo_root()


@task
def maintenance() -> None:
    """Update pinned package versions in template conda.yaml files."""

    allowlists = _load_allowlists()
    report = MaintenanceReport()

    # Update pinned package versions in template conda files.
    downloads_allowlist = allowlists.get("downloads", {})
    if downloads_allowlist:
        downloads_updater = DownloadsUpdater(downloads_allowlist, repo_root=REPO_ROOT, report=report)
        downloads_updater.update_targets()

    _write_report(report)


@task
def update_downloads_only() -> None:
    """Update pinned package versions in template conda.yaml files."""
    allowlists = _load_allowlists()
    report = MaintenanceReport()
    downloads_updater = DownloadsUpdater(
        allowlists.get("downloads", {}),
        repo_root=REPO_ROOT,
        report=report,
    )
    downloads_updater.update_targets()
    _write_report(report)


def _load_allowlists() -> Dict[str, Dict[str, dict]]:
    allowlists_dir = ROBOT_ROOT / "allowlists"
    return {
        "downloads": load_allowlist(allowlists_dir / "downloads.json"),
    }


def _resolve_output_dir() -> Path:
    output_dir = get_output_dir()
    if output_dir is not None:
        return output_dir.resolve()
    # Fallback keeps behavior when this module is invoked outside task execution.
    return Path(os.getenv("ROBOT_ARTIFACTS", str(ROBOT_ROOT / "output"))).resolve()


def _current_task_name() -> str:
    current_task = get_current_task()
    if current_task is None:
        return "<outside-task>"
    return current_task.name


def _write_report(report: MaintenanceReport) -> None:
    output_dir = _resolve_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "maintenance_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    logging.info("Wrote maintenance report for task '%s' to %s", _current_task_name(), report_path)
