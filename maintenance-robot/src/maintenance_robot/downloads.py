from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Optional

from packaging.version import InvalidVersion, Version

from .pypi_api import fetch_latest_version as fetch_pypi_version
from .reporter import DownloadUpdate, MaintenanceReport

logger = logging.getLogger(__name__)


class DownloadsUpdater:
    """Update pinned package versions in allowlisted files."""

    def __init__(
        self,
        allowlist: Dict[str, dict],
        repo_root: Path,
        report: MaintenanceReport,
    ) -> None:
        self.allowlist = allowlist
        self.repo_root = repo_root
        self.report = report

    def update_targets(self) -> None:
        logger.info("Processing %d package targets", len(self.allowlist))
        for identifier, config in self.allowlist.items():
            source = config.get("source", "pypi")
            if source != "pypi":
                logger.warning("Skipping %s: unsupported source '%s'", identifier, source)
                continue

            package = config.get("package")
            if not package:
                logger.warning("Skipping %s: missing 'package'", identifier)
                continue

            include_prerelease = bool(config.get("include_prerelease", False))
            max_major = config.get("max_major")
            version_format = config.get("version_format", "full")

            package_info = fetch_pypi_version(
                package=package,
                include_prerelease=include_prerelease,
                max_major=max_major,
            )
            if package_info is None:
                logger.warning("Skipping %s: unable to resolve latest version", identifier)
                continue

            latest = package_info.version
            targets = config.get("targets", [])
            if not targets:
                logger.warning("Skipping %s: no targets configured", identifier)
                continue

            for target in targets:
                file_name = target.get("file")
                if not file_name:
                    logger.warning("Skipping %s target: missing 'file'", identifier)
                    continue

                path = self.repo_root / file_name
                if not path.exists():
                    logger.warning("Target file does not exist for %s: %s", identifier, path)
                    continue

                patterns = target.get("patterns", [target.get("pattern")])
                if not patterns or patterns == [None]:
                    logger.warning("Skipping %s target with no patterns: %s", identifier, path)
                    continue

                for pattern_str in patterns:
                    pattern = re.compile(pattern_str, re.MULTILINE)
                    self._update_file(path, pattern, identifier, latest, version_format)

    def _update_file(
        self,
        path: Path,
        pattern: re.Pattern[str],
        identifier: str,
        latest_version: Version,
        version_format: str,
    ) -> None:
        text = path.read_text(encoding="utf-8")
        matches = list(pattern.finditer(text))
        if not matches:
            return

        updates_made = 0
        new_text = text
        previous_version: Optional[str] = None
        updated_version: Optional[str] = None

        for match in reversed(matches):
            current_raw = match.group("version")
            current_version = self._to_version(current_raw)
            if current_version is None:
                logger.warning("Skipping unparsable version '%s' in %s", current_raw, path)
                continue

            replacement = self._format_version(latest_version, version_format)
            if not self._needs_update(latest_version, current_version, version_format):
                continue

            if previous_version is None:
                previous_version = current_raw

            start, end = match.span()
            old_segment = new_text[start:end]
            new_segment = old_segment.replace(current_raw, replacement)
            new_text = new_text[:start] + new_segment + new_text[end:]
            updated_version = replacement
            updates_made += 1

        if updates_made == 0:
            return

        path.write_text(new_text, encoding="utf-8")
        assert previous_version is not None and updated_version is not None
        self.report.add_download_update(
            DownloadUpdate(
                file=path,
                identifier=identifier,
                previous=previous_version,
                updated=updated_version,
            )
        )
        logger.info(
            "Updated %d occurrence(s) of %s in %s (%s -> %s)",
            updates_made,
            identifier,
            path,
            previous_version,
            updated_version,
        )

    @staticmethod
    def _needs_update(latest: Version, current: Version, version_format: str) -> bool:
        if version_format == "major_only":
            return latest.major > current.major
        if version_format == "major_minor":
            return (latest.major, latest.minor) > (current.major, current.minor)
        return latest > current

    @staticmethod
    def _format_version(version: Version, version_format: str) -> str:
        if version_format == "major_only":
            return str(version.major)
        if version_format == "major_minor":
            return f"{version.major}.{version.minor}"
        return str(version)

    @staticmethod
    def _to_version(raw: str) -> Optional[Version]:
        trimmed = raw.lstrip("vV")
        try:
            return Version(trimmed)
        except InvalidVersion:
            return None
