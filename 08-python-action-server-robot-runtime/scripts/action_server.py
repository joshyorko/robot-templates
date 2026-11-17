"""Exercise a real, loopback Actions Runtime through its robot API using stdlib HTTP."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

try:
    from .setup_action_server import LocalActionServer, RuntimeSetupError
except ImportError:  # direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from setup_action_server import LocalActionServer, RuntimeSetupError


TERMINAL_STATUSES = {"PASSED", "FAILED", "CANCELLED", "ERROR"}
NUMERIC_RUN_STATUSES = {
    0: "NOT_RUN",
    1: "RUNNING",
    2: "PASSED",
    3: "FAILED",
    4: "CANCELLED",
}


def _normalize_status(status: Any) -> str:
    if isinstance(status, int) and not isinstance(status, bool):
        return NUMERIC_RUN_STATUSES.get(status, f"UNKNOWN_{status}")
    return str(status or "UNKNOWN").upper()


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class ActionServerError(RuntimeError):
    """A failed Actions Runtime request or protocol response."""


Transport = Callable[[str, str, bytes | None, dict[str, str], float], HttpResponse]


def _stdlib_transport(
    method: str,
    url: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResponse(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code, dict(error.headers.items()), error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ActionServerError(f"{method} request failed: {error}") from error


class ActionServerClient:
    """Small JSON/multipart client for the published robot and run API routes."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30,
        transport: Transport = _stdlib_transport,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
    ) -> HttpResponse:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
        if body is not None:
            headers["Content-Type"] = content_type
        response = self.transport(
            method,
            f"{self.base_url}{path}",
            body,
            headers,
            self.timeout,
        )
        if not 200 <= response.status < 300:
            detail = response.body.decode("utf-8", "replace")[:1000]
            raise ActionServerError(f"{method} {path} returned HTTP {response.status}: {detail}")
        return response

    def get_json(self, path: str) -> Any:
        return self.request("GET", path).json()

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        return self.request("POST", path, body=body).json()

    def import_robot(self, archive: Path) -> dict[str, Any]:
        boundary = f"robot-template-{uuid.uuid4().hex}"
        body = b"".join(
            (
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="file"; filename="robot.zip"\r\n',
                b"Content-Type: application/zip\r\n\r\n",
                archive.read_bytes(),
                f"\r\n--{boundary}--\r\n".encode(),
            )
        )
        return self.request(
            "POST",
            "/api/robots/import",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        ).json()

    def run_robot(
        self,
        robot_package_path: str,
        task_name: str,
        inputs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.post_json(
            "/api/robots/run",
            {
                "robot_package_path": robot_package_path,
                "task_name": task_name,
                "inputs": inputs or {},
            },
        )

    def wait_for_run(self, run_id: str, *, timeout: float, interval: float = 0.5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            latest = self.get_json(f"/api/runs/{urllib.parse.quote(run_id, safe='')}")
            status = _normalize_status(latest.get("status"))
            if status in TERMINAL_STATUSES:
                return latest
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"run {run_id} did not reach terminal state before timeout")

    def collect_artifacts(self, run_id: str, destination: Path) -> dict[str, Any]:
        encoded_id = urllib.parse.quote(run_id, safe="")
        response = self.get_json(f"/api/runs/{encoded_id}/artifacts")
        if not isinstance(response, list):
            raise ActionServerError("run artifacts endpoint did not return a list")
        destination.mkdir(parents=True, exist_ok=True)
        saved: list[dict[str, Any]] = []
        for item in response:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ActionServerError("run artifacts endpoint returned an invalid item")
            name = item["name"]
            relative = PurePosixPath(name)
            if (
                not name
                or "\\" in name
                or relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ActionServerError(f"unsafe artifact name returned by server: {name!r}")
            query = urllib.parse.urlencode({"artifact_name": name})
            content = self.request(
                "GET",
                f"/api/runs/{encoded_id}/artifacts/binary-content?{query}",
            ).body
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            saved.append(
                {"name": name, "size_in_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            )
        names = {item["name"] for item in saved}
        return {
            "files": saved,
            "log_files": [
                name for name in sorted(names)
                if name.endswith(".robolog") or name in {"__action_server_output.txt", "log.html"}
            ],
            "required_robot_outputs_missing": [
                name for name in ("report.json", "results.csv") if name not in names
            ],
        }


def _template_root(root: Path) -> Path:
    root = root.resolve()
    if (root / "robot.yaml").is_file():
        return root
    candidate = root / "08-python-action-server-robot-runtime"
    if (candidate / "robot.yaml").is_file():
        return candidate
    raise FileNotFoundError(f"robot.yaml not found under {root}")


def _load_workload(root: Path):
    validator_path = root / "invoice_robot" / "validator.py"
    spec = importlib.util.spec_from_file_location("_robot_runtime_validator", validator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load workload validator: {validator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _make_robot_archive(root: Path, archive: Path, source_paths: tuple[str, ...]) -> str:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for relative in source_paths:
            path = root / relative
            if not path.is_file():
                raise FileNotFoundError(f"Robot source archive input is missing: {relative}")
            output.write(path, arcname=relative)
        gitignore = root / ".gitignore"
        if gitignore.is_file():
            output.write(gitignore, arcname=".gitignore")
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def _local_rcc_identity(rcc: str) -> dict[str, Any]:
    executable = shutil.which(rcc) or rcc
    try:
        result = subprocess.run(
            [executable, "--version"], check=False, capture_output=True, text=True, timeout=10
        )
        return {
            "path": str(Path(executable).resolve()),
            "version_output": (result.stdout or result.stderr).strip(),
            "exit_code": result.returncode,
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"path": executable, "error": str(error)}


def _save_local_robot_outputs(imported_root: Path, destination: Path) -> dict[str, Any]:
    output_dir = imported_root / "output"
    copied: list[dict[str, Any]] = []
    if not output_dir.is_dir():
        return {"files": copied, "directory_exists": False}
    for name in ("report.json", "results.csv", "failure.json"):
        source = output_dir / name
        if source.is_file():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append({"name": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    return {"files": copied, "directory_exists": True}


def _clear_local_robot_outputs(imported_root: Path) -> None:
    """Remove only generated output inside this run's private imported robot."""
    output_dir = imported_root / "output"
    if output_dir.is_symlink():
        raise ActionServerError("imported robot output directory unexpectedly became a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    for child in output_dir.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def _run_one(
    client: ActionServerClient,
    imported_root: Path,
    evidence_dir: Path,
    task_name: str,
    inputs: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    _clear_local_robot_outputs(imported_root)
    submitted = client.run_robot(str(imported_root), task_name, inputs)
    run_id = submitted.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ActionServerError(f"robot run response did not include run_id: {submitted!r}")
    run_dir = evidence_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "submitted.json", submitted)
    detail = client.wait_for_run(run_id, timeout=timeout)
    _write_json(run_dir / "run.json", detail)
    artifacts = client.collect_artifacts(run_id, run_dir / "api-artifacts")
    _write_json(run_dir / "artifacts.json", artifacts)
    local_outputs = _save_local_robot_outputs(imported_root, run_dir / "imported-robot-output")
    return {
        "run_id": run_id,
        "submitted_status": submitted.get("status"),
        "status": _normalize_status(detail.get("status")),
        "duration_seconds": round(time.monotonic() - started, 3),
        "run_detail": detail,
        "artifacts": artifacts,
        "imported_robot_outputs": local_outputs,
        "evidence_dir": str(run_dir),
    }


def _terminal_status_ok(run: dict[str, Any], expected: str) -> bool:
    return run.get("status") == expected


def _execute_mode(
    root: Path,
    evidence_dir: Path,
    rcc: str,
    *,
    client: ActionServerClient,
    runtime_info: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    workload = _load_workload(root)
    source_sha = workload.source_digest(root)
    archive = evidence_dir / "robot-upload.zip"
    archive_sha = _make_robot_archive(root, archive, tuple(workload.SOURCE_PATHS))
    catalog_before = client.get_json("/api/robots/catalog")
    import_result = client.import_robot(archive)
    _write_json(evidence_dir / "catalog-before.json", catalog_before)
    _write_json(evidence_dir / "import.json", import_result)
    if not import_result.get("success"):
        raise ActionServerError(f"robot import failed: {import_result.get('message', 'unknown error')}")
    imported_path_value = import_result.get("robot_path")
    if not isinstance(imported_path_value, str) or not imported_path_value:
        raise ActionServerError("robot import response did not include robot_path")
    imported_root = Path(imported_path_value).resolve()
    private_home = runtime_info.get("private_home")
    if private_home and not imported_root.is_relative_to(Path(private_home) / ".robots"):
        raise ActionServerError("import returned a path outside the private robots directory")
    if workload.source_digest(imported_root) != source_sha:
        raise ActionServerError("imported robot source digest differs from reviewed source")
    catalog_after = client.get_json("/api/robots/catalog")
    _write_json(evidence_dir / "catalog-after.json", catalog_after)
    catalog_paths = {
        str(robot.get("path")) for robot in catalog_after.get("robots", []) if isinstance(robot, dict)
    }
    catalog_visible = str(imported_root) in catalog_paths

    input_path = imported_root / "devdata" / "invoices-alternate.json"
    expected_alternate = imported_root / "devdata" / "expected-alternate.json"
    if not input_path.is_file() or not expected_alternate.is_file():
        raise FileNotFoundError("imported robot is missing the alternate fixture or its expected ground truth")
    input_value = str(input_path)
    baseline_run = _run_one(
        client,
        imported_root,
        evidence_dir,
        "ValidateBatch",
        {},
        timeout,
    )
    try:
        baseline_output_check = workload.verify_outputs(
            imported_root / "output", imported_root / "devdata" / "invoices.json"
        )
        baseline_output_check_status = "PASS"
    except (OSError, ValueError, KeyError) as error:
        baseline_output_check = {"error": str(error)}
        baseline_output_check_status = "FAIL"
    _write_json(
        Path(baseline_run["evidence_dir"]) / "default-output-check.json",
        baseline_output_check,
    )

    input_run = _run_one(
        client,
        imported_root,
        evidence_dir,
        "ValidateBatch",
        {"INVOICE_INPUT": input_value},
        timeout,
    )
    try:
        output_check = workload.verify_outputs(imported_root / "output", input_path)
        output_check_status = "PASS"
    except (OSError, ValueError, KeyError) as error:
        output_check = {"error": str(error)}
        output_check_status = "FAIL"
    _write_json(Path(input_run["evidence_dir"]) / "alternate-output-check.json", output_check)

    request_inputs = input_run["run_detail"].get("inputs")
    if isinstance(request_inputs, str):
        try:
            request_inputs = json.loads(request_inputs)
        except json.JSONDecodeError:
            request_inputs = None
    inputs_recorded = request_inputs == {"INVOICE_INPUT": input_value}
    inputs_forwarded = (
        output_check_status == "PASS"
        and baseline_output_check_status == "PASS"
        and output_check.get("invoice_count") == 2
        and baseline_output_check.get("invoice_count") == 3
        and output_check.get("report_sha256") != baseline_output_check.get("report_sha256")
        and output_check.get("results_sha256") != baseline_output_check.get("results_sha256")
    )

    failure_run = _run_one(
        client,
        imported_root,
        evidence_dir,
        "FailBatch",
        {},
        timeout,
    )
    failure_file = imported_root / "output" / "failure.json"
    failure_output = None
    if failure_file.is_file():
        failure_output = json.loads(failure_file.read_text(encoding="utf-8"))
        _write_json(Path(failure_run["evidence_dir"]) / "local-failure.json", failure_output)

    required_reports_in_api = (
        not input_run["artifacts"]["required_robot_outputs_missing"]
    )
    api_output_checks = {}
    for name, run, fixture in [
        ("baseline", baseline_run, "invoices.json"),
        ("alternate", input_run, "invoices-alternate.json"),
    ]:
        try:
            api_output_checks[name] = workload.verify_outputs(
                Path(run["evidence_dir"]) / "api-artifacts",
                imported_root / "devdata" / fixture,
            )
        except (OSError, ValueError, KeyError) as error:
            api_output_checks[name] = {"error": str(error)}
    persisted_records = []
    datadir = runtime_info.get("private_datadir")
    runs = [baseline_run, input_run, failure_run]
    if datadir:
        for database in Path(datadir).glob("*.db"):
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                if not connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='run'"
                ).fetchone():
                    continue
                persisted_records = [dict(row) for row in connection.execute(
                    "SELECT id,status,inputs,robot_package_path,robot_task_name FROM run "
                    "WHERE id IN (?,?,?)", [run["run_id"] for run in runs]
                )]
                break
    persisted_by_id = {row["id"]: row for row in persisted_records}
    persisted_ok = all(
        run["run_id"] in persisted_by_id
        and _normalize_status(persisted_by_id[run["run_id"]]["status"]) == run["status"]
        and persisted_by_id[run["run_id"]]["inputs"] == run["run_detail"]["inputs"]
        for run in runs
    )
    _write_json(evidence_dir / "persisted-runs.json", persisted_records)
    logs_persisted = bool(input_run["artifacts"]["log_files"])
    checks = {
        "catalog_visible": catalog_visible,
        "import_succeeded": bool(import_result.get("success")),
        "default_validation_run_passed": _terminal_status_ok(baseline_run, "PASSED"),
        "default_output_matches_ground_truth": baseline_output_check_status == "PASS",
        "validation_run_passed": _terminal_status_ok(input_run, "PASSED"),
        "input_values_recorded": inputs_recorded,
        "input_values_reached_task": inputs_forwarded,
        "failure_run_failed": _terminal_status_ok(failure_run, "FAILED"),
        "failure_artifact_written": isinstance(failure_output, dict)
        and failure_output.get("status") == "FAIL",
        "report_and_csv_persisted_in_api_artifacts": required_reports_in_api,
        "run_logs_persisted_in_api_artifacts": logs_persisted,
        "downloaded_outputs_match_fixed_truth": all(
            item.get("ground_truth_match") is True for item in api_output_checks.values()
        ),
        "independent_database_records_match_api": persisted_ok,
        "imported_source_digest_matches": workload.source_digest(imported_root) == source_sha,
    }
    reasons = []
    if not inputs_recorded:
        reasons.append("RUN_INPUTS_NOT_RECORDED")
    if not inputs_forwarded:
        reasons.append("ROBOT_INPUTS_NOT_FORWARDED")
    if not required_reports_in_api:
        reasons.append("ROBOT_OUTPUTS_NOT_PERSISTED_BY_RUN_API")
    status = "PASS" if all(checks.values()) else "FAIL"
    if status == "PASS":
        reason = "ALL_ROBOT_API_ACCEPTANCE_CHECKS_PASSED"
    else:
        reason = ";".join(reasons) or "ROBOT_API_ACCEPTANCE_CHECK_FAILED"

    summary = {
        "mode": "B",
        "status": status,
        "reason": reason,
        "source_digest": source_sha,
        "upload_zip_sha256": archive_sha,
        "runtime": runtime_info,
        "requested_rcc": _local_rcc_identity(rcc),
        "api": {
            "base_url": client.base_url,
            "catalog_before_count": len(catalog_before.get("robots", [])),
            "catalog_after_count": len(catalog_after.get("robots", [])),
            "import": import_result,
        },
        "checks": checks,
        "inputs_test": {
            "status": "PASS" if inputs_forwarded and inputs_recorded else "FAIL",
            "requested": {"INVOICE_INPUT": input_value},
            "recorded_on_run": inputs_recorded,
            "reached_task_output": inputs_forwarded,
            "output_check": output_check,
            "baseline_output_check": baseline_output_check,
            "baseline_invoice_count": 3,
            "expected_invoice_count": 2,
            "baseline_run_id": baseline_run["run_id"],
        },
        "runs": {"baseline": baseline_run, "validation": input_run, "failure": failure_run},
        "api_output_checks": api_output_checks,
        "persisted_records": persisted_records,
        "durations_seconds": {
            "baseline": baseline_run["duration_seconds"],
            "validation": input_run["duration_seconds"],
            "failure": failure_run["duration_seconds"],
        },
        "evidence_paths": {
            "catalog_before": str(evidence_dir / "catalog-before.json"),
            "catalog_after": str(evidence_dir / "catalog-after.json"),
            "import": str(evidence_dir / "import.json"),
            "baseline_run": baseline_run["evidence_dir"],
            "validation_run": input_run["evidence_dir"],
            "failure_run": failure_run["evidence_dir"],
        },
    }
    _write_json(evidence_dir / "mode-b.json", summary)
    return summary


def _blocked(evidence_dir: Path, reason: str, **extra: Any) -> dict[str, Any]:
    summary = {
        "mode": "B",
        "status": "BLOCKED",
        "reason": reason,
        "evidence_paths": {"server_log": str(evidence_dir / "server.log")},
        **extra,
    }
    _write_json(evidence_dir / "mode-b.json", summary)
    return summary


def _loopback_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    host = parsed.hostname.lower()
    if host == "localhost":
        return True
    try:
        return __import__("ipaddress").ip_address(host).is_loopback
    except ValueError:
        return False


def run_mode(
    root: Path,
    evidence_dir: Path,
    rcc: str = "rcc",
    *,
    server_url: str | None = None,
    timeout: float = 900,
) -> dict[str, Any]:
    """Run B through the real loopback API, recording actual outcomes and evidence."""
    root = _template_root(Path(root))
    evidence_dir = Path(evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if server_url is not None and not _loopback_url(server_url):
        return _blocked(evidence_dir, "EXTERNAL_ACTION_SERVER_URL_REJECTED")

    if server_url is not None:
        api_key = os.environ.get("ACTIONS_RUNTIME_API_KEY")
        if not api_key:
            return _blocked(evidence_dir, "ACTIONS_RUNTIME_API_KEY_REQUIRED_FOR_EXTERNAL_LOOPBACK_SERVER")
        runtime_info = {"runtime_version": "external", "server_url": server_url}
        client = ActionServerClient(server_url, api_key, timeout=min(timeout, 600))
        try:
            return _execute_mode(root, evidence_dir, rcc, client=client, runtime_info=runtime_info, timeout=timeout)
        except (ActionServerError, OSError, ValueError, TimeoutError, RuntimeError) as error:
            return _blocked(evidence_dir, "API_EXECUTION_FAILED", error=str(error), runtime=runtime_info)

    try:
        runtime_work_dir = Path(
            os.environ.get("ACTIONS_RUNTIME_WORK_DIR", evidence_dir / "action-server")
        ).resolve()
        server = LocalActionServer(runtime_work_dir, timeout=min(timeout, 180))
        with server:
            runtime_info = dict(server.runtime)
            runtime_info.update(
                {
                    "session_id": server.session_id,
                    "server_pid": server.process.pid,
                    "server_url": server.base_url,
                    "private_home": str(server.home),
                    "private_robots_home": str(server.robots_home),
                    "private_datadir": str(server.datadir),
                    "server_log": str(server.server_log),
                }
            )
            client = ActionServerClient(server.base_url, server.api_key, timeout=min(timeout, 600))
            result = _execute_mode(
                root,
                evidence_dir,
                rcc,
                client=client,
                runtime_info=runtime_info,
                timeout=timeout,
            )
        result["runtime"]["server_exit_code"] = server.process.returncode
        _write_json(evidence_dir / "mode-b.json", result)
        return result
    except (ActionServerError, RuntimeSetupError, OSError, ValueError, TimeoutError, RuntimeError) as error:
        paths = {"server_log": str(server.server_log)} if "server" in locals() and hasattr(
            server, "server_log"
        ) else {}
        return _blocked(evidence_dir, "LOCAL_ACTION_SERVER_OR_API_FAILED", error=str(error),
                        evidence_paths=paths)
