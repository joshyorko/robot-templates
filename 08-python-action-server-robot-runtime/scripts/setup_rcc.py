#!/usr/bin/env python3
"""Download the Linux RCC release pinned by rcc-lock.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path


LOCK_PATH = Path(__file__).resolve().parents[1] / "rcc-lock.json"
EXPECTED_URL = "https://github.com/joshyorko/rcc/releases/download/v18.19.7/rcc-linux64"
MAX_RCC_BYTES = 128 * 1024 * 1024


def load_lock(path: Path = LOCK_PATH) -> dict[str, str]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    required = {"version", "asset", "url", "sha256", "platform"}
    if set(lock) != required:
        raise ValueError("rcc-lock.json has an unexpected shape")
    if lock["platform"] != "linux_amd64":
        raise ValueError("this setup script only supports the pinned linux_amd64 RCC")
    if lock["asset"] != "rcc-linux64" or lock["url"] != EXPECTED_URL:
        raise ValueError("rcc-lock.json does not point to the qualified GitHub release asset")
    if len(lock["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in lock["sha256"]):
        raise ValueError("rcc-lock.json sha256 must be lowercase hexadecimal")
    return lock


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_rcc(rcc: str | Path, lock_path: Path = LOCK_PATH) -> dict[str, str]:
    """Verify an RCC executable against the pinned release and return its identity."""
    lock = load_lock(lock_path)
    value = str(rcc)
    candidate = shutil.which(value) if os.sep not in value else value
    if not candidate:
        raise FileNotFoundError(f"RCC executable not found: {value}")
    binary = Path(candidate).resolve(strict=True)
    actual = sha256_file(binary)
    if actual != lock["sha256"]:
        raise RuntimeError(f"RCC release digest mismatch: expected {lock['sha256']}, got {actual}")
    version = subprocess.run([str(binary), "version"], check=True, text=True, capture_output=True).stdout.strip()
    expected = f"v{lock['version']}"
    if version != expected:
        raise RuntimeError(f"RCC reports {version!r}; expected {expected}")
    return {"path": str(binary), "version": version, "sha256": actual, "platform": lock["platform"]}


def install(destination: Path, lock_path: Path = LOCK_PATH) -> Path:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("the pinned RCC binary is for Linux amd64")
    lock = load_lock(lock_path)
    destination = destination.expanduser()
    if destination.is_symlink():
        raise RuntimeError("refusing to replace a symlink RCC destination")
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.is_file() and sha256_file(destination) == lock["sha256"]:
        destination.chmod(0o755)
        verify_rcc(destination, lock_path)
        return destination

    fd, temporary_name = tempfile.mkstemp(prefix=".rcc-download-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(lock["url"], timeout=60) as response:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > MAX_RCC_BYTES:
                    raise RuntimeError("RCC release download exceeds the 128 MiB safety bound")
                output.write(block)
        actual = sha256_file(temporary)
        if actual != lock["sha256"]:
            raise RuntimeError(f"RCC release digest mismatch: expected {lock['sha256']}, got {actual}")
        temporary.chmod(0o755)
        verify_rcc(temporary, lock_path)
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path, help="explicit path for the verified RCC binary")
    parser.add_argument("--lock", type=Path, default=LOCK_PATH, help="override the checked-in RCC lock")
    args = parser.parse_args()
    print(install(args.destination, args.lock))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
