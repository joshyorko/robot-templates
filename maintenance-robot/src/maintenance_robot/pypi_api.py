from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import requests
from packaging.version import InvalidVersion, Version
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

PYPI_API_ROOT = "https://pypi.org/pypi"


class PyPIAPIError(RuntimeError):
    """Raised when PyPI API responses cannot be parsed."""


@dataclass(frozen=True)
class PackageInfo:
    version: Version
    version_str: str


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(4),
    retry=retry_if_exception_type((requests.RequestException, PyPIAPIError)),
)
def _get(url: str) -> dict:
    response = requests.get(url, timeout=30)
    if response.status_code >= 400:
        raise PyPIAPIError(f"PyPI API error {response.status_code}: {response.text}")
    try:
        data = response.json()
    except ValueError as exc:
        raise PyPIAPIError("Failed to decode JSON from PyPI API") from exc
    if not isinstance(data, dict):
        raise PyPIAPIError("Expected dict response from PyPI API")
    return data


@lru_cache(maxsize=128)
def fetch_latest_version(
    package: str,
    include_prerelease: bool = False,
    max_major: Optional[int] = None,
) -> Optional[PackageInfo]:
    """Fetch the latest version for a PyPI package respecting constraints."""
    url = f"{PYPI_API_ROOT}/{package}/json"
    data = _get(url)

    # PyPI returns latest stable version in 'info' by default
    if not include_prerelease:
        latest = data.get("info", {}).get("version")
        if latest:
            try:
                version = Version(latest)
                if max_major is not None and version.major > max_major:
                    logger.debug(
                        "Latest version %s exceeds max_major=%s, checking releases",
                        latest,
                        max_major,
                    )
                else:
                    return PackageInfo(version=version, version_str=latest)
            except InvalidVersion:
                logger.warning("Invalid version from PyPI for %s: %s", package, latest)

    # Fallback: parse all releases and find the best match
    releases = data.get("releases", {})
    valid_versions: list[tuple[Version, str]] = []

    for version_str in releases:
        try:
            version = Version(version_str)
            # Skip prereleases if not allowed
            if not include_prerelease and version.is_prerelease:
                continue
            # Skip versions exceeding max_major
            if max_major is not None and version.major > max_major:
                continue
            valid_versions.append((version, version_str))
        except InvalidVersion:
            logger.debug("Skipping invalid version: %s", version_str)

    if not valid_versions:
        return None

    # Sort and return the latest
    valid_versions.sort(reverse=True)
    version, version_str = valid_versions[0]
    return PackageInfo(version=version, version_str=version_str)
