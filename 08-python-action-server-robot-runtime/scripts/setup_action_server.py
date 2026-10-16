#!/usr/bin/env python3
"""Prepare and launch an isolated, pinned Actions Runtime for the B lab."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCK = TEMPLATE_ROOT / "runtime-lock.json"
TERMINAL_RUNTIME_VERSION = "1.0.2"


class RuntimeSetupError(RuntimeError):
    """The pinned Actions Runtime could not be prepared or started."""


def _run(argv: list[str], *, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeSetupError(f"command failed: {Path(argv[0]).name}: {error}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_wheel(package: dict[str, str], wheel_dir: Path) -> Path:
    wheel_dir.mkdir(parents=True, exist_ok=True)
    wheel = wheel_dir / package["filename"]
    if wheel.is_file() and _sha256(wheel) == package["sha256"]:
        return wheel

    temporary = wheel.with_suffix(wheel.suffix + ".download")
    try:
        request = urllib.request.Request(
            package["url"], headers={"User-Agent": "robot-template-08/1.0"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            with temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
        actual = _sha256(temporary)
        if actual != package["sha256"]:
            raise RuntimeSetupError(
                f"SHA-256 mismatch for {package['filename']}: {actual}"
            )
        os.replace(temporary, wheel)
    except (OSError, urllib.error.URLError) as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeSetupError(
            f"could not download pinned wheel {package['filename']}: {error}"
        ) from error
    return wheel


def prepare_runtime(work_dir: Path) -> dict[str, Any]:
    """Create a private Python 3.13 environment from hash-verified wheels."""
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeSetupError("runtime-lock.json currently supports Linux x86_64 only")
    lock = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeSetupError("uv is required to create the temporary runtime venv")

    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = work_dir / "venv"
    python = venv_dir / "bin" / "python"
    runtime = venv_dir / "bin" / "action-server"
    if not python.exists():
        result = _run([uv, "venv", "--python", lock["python"], str(venv_dir)])
        if result.returncode:
            raise RuntimeSetupError(result.stderr.strip() or "uv venv failed")

    wheels = [_download_wheel(package, work_dir / "wheels") for package in lock["packages"]]
    result = _run([uv, "pip", "install", "--python", str(python), *map(str, wheels)])
    if result.returncode:
        raise RuntimeSetupError(result.stderr.strip() or "uv pip install failed")

    result = _run([str(runtime), "version"])
    if result.returncode or result.stdout.strip() != TERMINAL_RUNTIME_VERSION:
        raise RuntimeSetupError(
            f"unexpected action-server version: {result.stdout.strip() or result.stderr.strip()}"
        )

    probe = _run(
        [
            str(python),
            "-c",
            "import actions.server._api_robots as r; "
            "import actions.server._rcc_robot_utils as u; "
            "import actions.server._download_rcc as d; "
            "import actions.server._rcc_runtime_adapter as a; "
            "from pathlib import Path; "
            "print(Path(r.__file__).resolve()); print(Path(u.__file__).resolve()); "
            "print(Path(d.__file__).resolve()); print(Path(a.__file__).resolve()); "
            "print('BUNDLED_RCC_VERSION=' + d.RCC_VERSION)",
        ]
    )
    if probe.returncode:
        raise RuntimeSetupError("pinned wheel lacks the robot API/helper source")
    source_paths = [Path(line) for line in probe.stdout.splitlines() if line.startswith("/")]
    source_hashes = {path.name: _sha256(path) for path in source_paths}
    bundled_rcc_version = next(
        line.split("=", 1)[1]
        for line in probe.stdout.splitlines()
        if line.startswith("BUNDLED_RCC_VERSION=")
    )
    return {
        "python": lock["python"],
        "runtime_version": result.stdout.strip(),
        "bundled_rcc_version": bundled_rcc_version,
        "bundled_rcc_sha256": lock["bundled_rcc"]["sha256"],
        "bundled_rcc_release_url": lock["bundled_rcc"]["url"],
        "bundled_rcc_expected_path": str(
            Path(source_paths[2]).parent / "bin" / f"rcc-{bundled_rcc_version}"
        ),
        "runtime_venv": str(venv_dir),
        "wheel_sha256": {p["name"]: p["sha256"] for p in lock["packages"]},
        "source_sha256": source_hashes,
        "transitive_dependencies": "resolved by uv during setup; not hash-locked",
    }


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _readiness(base_url: str, api_key: str, process: subprocess.Popen[Any], deadline: float) -> None:
    endpoint = f"{base_url}/api/robots/catalog"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeSetupError(f"Action Server exited early with code {process.returncode}")
        request = urllib.request.Request(
            endpoint, headers={"Authorization": f"Bearer {api_key}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.25)
    raise RuntimeSetupError("Action Server did not become ready before timeout")


@dataclass
class LocalActionServer:
    """Own one loopback Actions Runtime and all of its private persistent state."""

    work_dir: Path
    timeout: float = 60

    def __enter__(self) -> "LocalActionServer":
        self.work_dir = self.work_dir.resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.runtime = prepare_runtime(self.work_dir / "runtime")
        self.session_id = uuid.uuid4().hex
        self.state_dir = self.work_dir / "sessions" / self.session_id
        self.home = self.state_dir / "home"
        self.actions_home = self.state_dir / "actions-home"
        self.robots_home = Path(
            os.environ.get("ACTIONS_RUNTIME_ROBOTS_HOME", self.work_dir / "robots-home")
        ).resolve()
        self.datadir = self.state_dir / "datadir"
        self.actions_dir = self.state_dir / "action-packages"
        for directory in (
            self.home,
            self.actions_home,
            self.robots_home,
            self.datadir,
            self.actions_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self.api_key = secrets.token_urlsafe(32)
        self.base_url = f"http://127.0.0.1:{_unused_loopback_port()}"
        self.server_log = self.state_dir / "server.log"
        self.server_log_file = self.server_log.open("ab")
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "ACTIONS_HOME": str(self.actions_home),
                "ROBOTS_HOME": str(self.robots_home),
                "ROBOCORP_HOME": str(self.actions_home),
                "RCC_HOLOTREE_MODE": "private",
            }
        )
        for name in ("INVOICE_INPUT", "ROBOT_ARTIFACTS"):
            env.pop(name, None)
        command = [
            str(Path(self.runtime["runtime_venv"]) / "bin" / "action-server"),
            "start",
            "--address",
            "127.0.0.1",
            "--port",
            self.base_url.rsplit(":", 1)[1],
            f"--api-key={self.api_key}",
            "--dir",
            str(self.actions_dir),
            "--datadir",
            str(self.datadir),
            "--parent-pid",
            str(os.getpid()),
            "--skip-lint",
            "--full-openapi-spec",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=self.state_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self.server_log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _readiness(self.base_url, self.api_key, self.process, time.monotonic() + self.timeout)
            rcc_path = Path(self.runtime["bundled_rcc_expected_path"])
            rcc_sha256 = _sha256(rcc_path)
            if rcc_sha256 != self.runtime["bundled_rcc_sha256"]:
                raise RuntimeSetupError(
                    f"bundled RCC SHA-256 mismatch: {rcc_sha256}"
                )
            rcc_version = _run([str(rcc_path), "--version"], timeout=15)
            self.runtime["bundled_rcc_path"] = str(rcc_path)
            self.runtime["bundled_rcc_actual_sha256"] = rcc_sha256
            self.runtime["bundled_rcc_version_output"] = (
                rcc_version.stdout or rcc_version.stderr
            ).strip()
            self.runtime["bundled_rcc_version_exit_code"] = rcc_version.returncode
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        log_file = getattr(self, "server_log_file", None)
        if log_file is not None and not log_file.closed:
            log_file.close()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_runtime(args.work_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
