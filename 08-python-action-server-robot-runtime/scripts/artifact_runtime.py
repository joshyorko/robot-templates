#!/usr/bin/env python3
"""Exercise strict, offline RCC Environment Artifact execution for template 08."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Mapping


LOCK_PATH = Path(__file__).resolve().parents[1] / "rcc-lock.json"
ARTIFACT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PINNED_RCC_SHA256 = "f8e0394be7f47ee84d6f4cd734822b87b492a4d6102f977f6a18f29453703d48"
SIGNER_ID = "template-08-ephemeral-ed25519"
DEAD_PROVIDER = "http://127.0.0.1:9/"


class RuntimeFailure(RuntimeError):
    pass


class PinnedRccUnavailable(RuntimeFailure):
    pass


def _load_lock() -> dict[str, Any]:
    value = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if value.get("version") != "18.19.7" or value.get("sha256") != PINNED_RCC_SHA256:
        raise RuntimeFailure("rcc-lock.json does not match the qualified RCC release")
    return value


def _rcc_path(value: str) -> Path:
    candidate = shutil.which(value) if os.sep not in value else value
    if not candidate:
        raise PinnedRccUnavailable(f"RCC executable not found: {value}")
    return Path(candidate).resolve(strict=True)


def _verify_rcc(value: str) -> tuple[Path, str]:
    lock = _load_lock()
    binary = _rcc_path(value)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest != lock["sha256"]:
        raise PinnedRccUnavailable(f"RCC binary SHA-256 does not match pinned v{lock['version']}")
    version = subprocess.run([str(binary), "version"], check=True, text=True, capture_output=True).stdout.strip()
    if version != f"v{lock['version']}":
        raise PinnedRccUnavailable(f"RCC reports {version!r}; expected v{lock['version']}")
    return binary, digest


def _artifact_digest(result: Mapping[str, Any]) -> str:
    """Find RCC's artifact identity without accepting unrelated SHA fields."""
    candidates: list[Any] = []
    for key in ("artifact", "artifactDigest", "artifact_digest", "digest"):
        if key in result:
            candidates.append(result[key])
    for key in ("result", "identity", "environmentArtifact"):
        nested = result.get(key)
        if isinstance(nested, Mapping):
            try:
                return _artifact_digest(nested)
            except RuntimeFailure:
                pass
    for candidate in candidates:
        if isinstance(candidate, str) and ARTIFACT_RE.fullmatch(candidate):
            return candidate
        if isinstance(candidate, Mapping):
            for key in ("digest", "artifactDigest"):
                value = candidate.get(key)
                if isinstance(value, str) and ARTIFACT_RE.fullmatch(value):
                    return value
    raise RuntimeFailure("RCC publish result did not contain a canonical Environment Artifact digest")


def _json_result(stdout: str, step: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeFailure(f"{step} did not emit one JSON result: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeFailure(f"{step} JSON result is not an object")
    return value


def _source_api(root: Path):
    root_string = str(root)
    previous = {name: module for name, module in sys.modules.items() if name == "invoice_robot" or name.startswith("invoice_robot.")}
    for name in previous:
        del sys.modules[name]
    sys.path.insert(0, root_string)
    try:
        module = importlib.import_module("invoice_robot")
        module_path = Path(module.__file__).resolve()
        if root not in module_path.parents:
            raise RuntimeFailure("invoice_robot resolved outside the requested source root")
        source_digest = getattr(module, "source_digest", None)
        verify_outputs = getattr(module, "verify_outputs", None)
        if not callable(source_digest) or not callable(verify_outputs):
            raise RuntimeFailure("invoice_robot must export source_digest(root) and verify_outputs(output,input)")
        return source_digest, verify_outputs
    finally:
        try:
            sys.path.remove(root_string)
        except ValueError:
            pass
        for name in tuple(sys.modules):
            if name == "invoice_robot" or name.startswith("invoice_robot."):
                del sys.modules[name]
        sys.modules.update(previous)


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    logs: Path,
    step: str,
    expected_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(argv, cwd=cwd, env=dict(env), text=True, capture_output=True, check=False)
    (logs / f"{step}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (logs / f"{step}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    (logs / f"{step}.exit-code").write_text(f"{completed.returncode}\n", encoding="ascii")
    if expected_success and completed.returncode != 0:
        raise RuntimeFailure(f"{step} failed with exit code {completed.returncode}; see evidence logs")
    if not expected_success and completed.returncode == 0:
        raise RuntimeFailure(f"{step} unexpectedly succeeded")
    return completed


def _rcc_json(
    binary: Path,
    arguments: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    logs: Path,
    step: str,
) -> dict[str, Any]:
    completed = _run([str(binary), *arguments], cwd=cwd, env=env, logs=logs, step=step)
    result = _json_result(completed.stdout, step)
    (logs / f"{step}.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _private_robo_env(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["ROBOCORP_HOME"] = str(home)
    environment["RCC_HOLOTREE_MODE"] = "private"
    return environment


def _write_unknown_roots(path: Path) -> None:
    # A distinct syntactically valid key ID guarantees the trusted-root lookup rejects the signer.
    _write_trust_roots(path, "untrusted-template-08-key", b"\x01" * 32)


def _write_trust_roots(path: Path, key_id: str, public_key: bytes) -> None:
    if len(public_key) != 32:
        raise RuntimeFailure("Ed25519 trust roots require a raw 32-byte public key")
    roots = {key_id: base64.b64encode(public_key).decode("ascii").rstrip("=")}
    path.write_text(json.dumps(roots, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o644)


def _task_bootstrap(source_root: Path, input_path: Path, output_dir: Path) -> list[str]:
    """Launch the declared Robocorp task from explicit, verified source and output paths."""
    bootstrap = (
        "import os,runpy,sys; source,tasks,input_path,output_path=sys.argv[1:5]; "
        "sys.path.insert(0,source); os.environ['INVOICE_INPUT']=input_path; "
        "os.environ['ROBOT_ARTIFACTS']=output_path; "
        "sys.argv=['robocorp.tasks','run',tasks,'-t','validate_batch_task']; "
        "runpy.run_module('robocorp.tasks',run_name='__main__')"
    )
    return [
        "python", "-c", bootstrap, str(source_root), str(source_root / "tasks.py"),
        str(input_path), str(output_dir),
    ]


def _start_provider_server(binary: Path, provider_root: Path, home: Path, logs: Path):
    process = subprocess.Popen(
        [str(binary), "cache", "serve", "--root", str(provider_root), "--listen", "127.0.0.1:0", "--backend", "filesystem", "--json"],
        cwd=provider_root.parent.parent.parent,
        env=_private_robo_env(home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    startup_lines: queue.Queue[str] = queue.Queue()

    def copy_stream(stream, path: Path, startup: bool) -> None:
        with path.open("w", encoding="utf-8") as output:
            for line in iter(stream.readline, ""):
                output.write(line)
                output.flush()
                if startup:
                    startup_lines.put(line)

    stdout_thread = threading.Thread(target=copy_stream, args=(process.stdout, logs / "provider-server.stdout.txt", True), daemon=True)
    stderr_thread = threading.Thread(target=copy_stream, args=(process.stderr, logs / "provider-server.stderr.txt", False), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        startup = startup_lines.get(timeout=30)
        result = _json_result(startup, "provider server startup")
        url = result.get("url")
        parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"} or parsed.path not in {"", "/"}:
            raise RuntimeFailure("RCC cache serve did not return a loopback provider root URL")
        return process, url, stdout_thread, stderr_thread
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        raise


def _stop_provider_server(process: subprocess.Popen[str], stdout_thread: threading.Thread, stderr_thread: threading.Thread, logs: Path) -> int:
    if process.poll() is None:
        process.terminate()
        try:
            return_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
    else:
        return_code = process.returncode
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    (logs / "provider-server.exit-code").write_text(f"{return_code}\n", encoding="ascii")
    return return_code


def _acquire_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    summary = {key: result[key] for key in ("artifactDigest", "materializationId", "cacheHit", "path") if key in result}
    verification = result.get("verification")
    if isinstance(verification, Mapping):
        summary["verification"] = {
            key: verification[key]
            for key in ("valid", "code", "keyID", "policyMode", "platform", "builder", "decisionID")
            if key in verification
        }
    return summary


def _validate_exec_receipt(path: Path, artifact: str) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeFailure(f"RCC execution receipt is missing or malformed: {exc}") from exc
    if not isinstance(receipt, Mapping):
        raise RuntimeFailure("RCC execution receipt must be a JSON object")
    verification = receipt.get("verification") if isinstance(receipt, Mapping) else None
    if (
        receipt.get("artifactDigest") != artifact
        or receipt.get("status") != "completed"
        or receipt.get("exitCode") != 0
        or not receipt.get("leaseId")
        or not isinstance(verification, Mapping)
        or verification.get("valid") is not True
        or verification.get("policyMode") != "strict-remote"
    ):
        raise RuntimeFailure("RCC execution receipt does not prove a successful strict-remote leased execution")
    return {
        "artifactDigest": receipt["artifactDigest"],
        "status": receipt["status"],
        "exitCode": receipt["exitCode"],
        "leaseId": receipt["leaseId"],
        "cacheHit": receipt.get("cacheHit"),
        "verification": {
            key: verification[key]
            for key in ("valid", "code", "keyID", "policyMode", "decisionID")
            if key in verification
        },
    }


def _run_mode(root: Path, evidence_dir: Path, rcc: str) -> dict[str, Any]:
    root = root.resolve(strict=True)
    evidence_dir = evidence_dir.expanduser().resolve()
    if root == evidence_dir:
        raise RuntimeFailure("evidence_dir must not replace the template source root")
    if root in evidence_dir.parents:
        output_root = root / "output"
        ignored_output = root / ".gitignore"
        if output_root != evidence_dir and output_root not in evidence_dir.parents:
            raise RuntimeFailure("evidence inside the source root is allowed only below its ignored output/ directory")
        if not ignored_output.is_file() or "output/" not in ignored_output.read_text(encoding="utf-8").splitlines():
            raise RuntimeFailure("template output/ must be git-ignored before storing run evidence inside the source root")
    robot_yaml = root / "robot.yaml"
    input_path = root / "devdata" / "invoices.json"
    if not robot_yaml.is_file() or not input_path.is_file():
        raise RuntimeFailure("template root must contain robot.yaml and devdata/invoices.json")

    evidence_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    producer_home = evidence_dir / "producer-home"
    consumer_home = evidence_dir / "consumer-home"
    archive_home = evidence_dir / "archive-only-home"
    negative_home = evidence_dir / "unknown-signature-home"
    trust_carrier = evidence_dir / "detached-trust-carrier"
    deployment = evidence_dir / "deployment-trust"
    transport = evidence_dir / "transport"
    provider_output = evidence_dir / "provider-output"
    archive_output = evidence_dir / "archive-output"
    receipts = evidence_dir / "receipts"
    logs = evidence_dir / "logs"
    for directory in (producer_home, consumer_home, archive_home, negative_home, trust_carrier, deployment, transport, provider_output, archive_output, receipts, logs):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if len({producer_home, consumer_home, archive_home, negative_home}) != 4:
        raise RuntimeFailure("producer, provider consumer, archive consumer, and negative-test ROBOCORP_HOME roots must be distinct")

    source_digest, verify_outputs = _source_api(root)
    source_before = source_digest(root)
    if not isinstance(source_before, str) or not source_before:
        raise RuntimeFailure("invoice_robot.source_digest(root) returned no source identity")

    binary, rcc_sha = _verify_rcc(rcc)
    producer_env = _private_robo_env(producer_home)
    consumer_env = _private_robo_env(consumer_home)
    archive_env = _private_robo_env(archive_home)
    negative_env = _private_robo_env(negative_home)

    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeFailure("OpenSSL is required to create an ephemeral Ed25519 signing key")
    with tempfile.TemporaryDirectory(prefix="rcca-ed25519-") as key_tmp:
        private_pem = Path(key_tmp) / "ephemeral-ed25519.pem"
        private_der = Path(key_tmp) / "private.der"
        private_seed = Path(key_tmp) / "private.seed"
        public_der = Path(key_tmp) / "public.der"
        subprocess.run([openssl, "genpkey", "-algorithm", "Ed25519", "-out", str(private_pem)], check=True, capture_output=True, text=True)
        subprocess.run([openssl, "pkey", "-in", str(private_pem), "-pubout", "-outform", "DER", "-out", str(public_der)], check=True, capture_output=True, text=True)
        subprocess.run([openssl, "pkey", "-in", str(private_pem), "-outform", "DER", "-out", str(private_der)], check=True, capture_output=True, text=True)
        private_bytes = private_der.read_bytes()
        public_bytes = public_der.read_bytes()
        if private_bytes[:16] != bytes.fromhex("302e020100300506032b657004220420") or len(private_bytes) != 48:
            raise RuntimeFailure("OpenSSL did not produce the expected Ed25519 private-key encoding")
        if public_bytes[:12] != bytes.fromhex("302a300506032b6570032100") or len(public_bytes) != 44:
            raise RuntimeFailure("OpenSSL did not produce the expected Ed25519 public-key encoding")
        private_seed.write_bytes(private_bytes[-32:])
        private_seed.chmod(0o600)
        private_pem.unlink()
        private_der.unlink()
        private_bytes = b""
        trust_roots = deployment / "trust-roots.json"
        _write_trust_roots(trust_roots, SIGNER_ID, public_bytes[-32:])
        rcc_version = subprocess.run([str(binary), "version"], check=True, text=True, capture_output=True).stdout.strip()

        publish = _rcc_json(
            binary,
            [
                "env", "publish", "--robot", str(robot_yaml), "--provider", "local", "--json",
                "--signing-key", str(private_seed), "--signing-key-id", SIGNER_ID,
                "--trust-carrier", str(trust_carrier), "--trust-carrier-type", "filesystem",
            ],
            cwd=root, env=producer_env, logs=logs, step="publish",
        )
        artifact = _artifact_digest(publish)

        archive = transport / f"{artifact.removeprefix('sha256:')}.rcca"
        export = _run(
            [str(binary), "env", "export", "--artifact", artifact, "--provider", "local", "--output", str(archive)],
            cwd=root, env=producer_env, logs=logs, step="export",
        )
        try:
            export_json = _json_result(export.stdout, "export")
        except RuntimeFailure:
            export_json = {"stdout": export.stdout.strip()}
        (logs / "export.json").write_text(json.dumps(export_json, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not archive.is_file() or archive.stat().st_size == 0 or archive.stat().st_size > 256 * 1024 * 1024:
            raise RuntimeFailure("exported .rcca is absent, empty, or exceeds the 256 MiB workload limit")

        provider_root = producer_home / "artifacts" / "v1" / "provider"
        if not provider_root.is_dir():
            raise RuntimeFailure("RCC publish did not create the isolated filesystem provider root")
        provider_process, provider_url, provider_stdout_thread, provider_stderr_thread = _start_provider_server(
            binary, provider_root, evidence_dir / "provider-server-home", logs,
        )
        try:
            provider_acquire = _rcc_json(
                binary,
                [
                    "env", "acquire", "--artifact", artifact, "--provider", provider_url, "--strict-remote",
                    "--trust-carrier", str(trust_carrier), "--trust-carrier-type", "filesystem",
                    "--trust-roots", str(trust_roots), "--json",
                ],
                cwd=root, env=consumer_env, logs=logs, step="acquire-provider",
            )
        finally:
            provider_server_exit = _stop_provider_server(provider_process, provider_stdout_thread, provider_stderr_thread, logs)
        if provider_server_exit not in (0, -15, 143):
            raise RuntimeFailure(f"loopback provider server exited unexpectedly with code {provider_server_exit}")

        warm = _rcc_json(
            binary,
            [
                "env", "acquire", "--artifact", artifact, "--provider", provider_url,
                "--strict-remote", "--trust-carrier", str(trust_carrier), "--trust-carrier-type", "filesystem",
                "--trust-roots", str(trust_roots), "--json",
            ],
            cwd=root, env=consumer_env, logs=logs, step="warm-acquire-provider-down",
        )
        if warm.get("cacheHit") != "local-materialization":
            raise RuntimeFailure("provider-down warm acquisition did not report a local materialization cache hit")

        provider_receipt = receipts / "provider-artifact-exec.json"
        _run(
            [
                str(binary), "env", "exec", "--artifact", artifact, "--provider", provider_url,
                "--strict-remote", "--trust-carrier", str(trust_carrier), "--trust-carrier-type", "filesystem",
                "--trust-roots", str(trust_roots), "--inherit-streams", "--receipt-file", str(provider_receipt), "--",
                *_task_bootstrap(root, input_path, provider_output),
            ],
            cwd=root, env=consumer_env, logs=logs, step="provider-down-artifact-exec",
        )
        provider_receipt_result = _validate_exec_receipt(provider_receipt, artifact)
        provider_hashes = verify_outputs(provider_output, input_path)
        if provider_hashes.get("ground_truth_match") is not True:
            raise RuntimeFailure("provider-acquired task output did not match checked-in ground truth")

        archive_acquire = _rcc_json(
            binary,
            [
                "env", "acquire", "--archive", str(archive), "--strict-remote",
                "--trust-carrier", str(trust_carrier), "--trust-carrier-type", "filesystem",
                "--trust-roots", str(trust_roots), "--json",
            ],
            cwd=root, env=archive_env, logs=logs, step="acquire-archive-only",
        )
        archive_receipt = receipts / "archive-only-artifact-exec.json"
        _run(
            [
                str(binary), "env", "exec", "--artifact", artifact, "--strict-remote",
                "--trust-carrier", str(trust_carrier), "--trust-carrier-type", "filesystem",
                "--trust-roots", str(trust_roots), "--inherit-streams", "--receipt-file", str(archive_receipt), "--",
                *_task_bootstrap(root, input_path, archive_output),
            ],
            cwd=root, env=archive_env, logs=logs, step="archive-only-artifact-exec",
        )
        archive_receipt_result = _validate_exec_receipt(archive_receipt, artifact)
        archive_hashes = verify_outputs(archive_output, input_path)
        if archive_hashes.get("ground_truth_match") is not True:
            raise RuntimeFailure("archive-only task output did not match checked-in ground truth")
        if provider_hashes != archive_hashes:
            raise RuntimeFailure("provider-acquired and archive-only task outputs differ")
        source_after = source_digest(root)
        if source_after != source_before:
            raise RuntimeFailure("source digest changed during artifact-backed execution")

        unknown_roots = deployment / "unknown-trust-roots.json"
        _write_unknown_roots(unknown_roots)
        negative = _run(
            [
                str(binary), "env", "acquire", "--archive", str(archive), "--strict-remote",
                "--trust-carrier", str(trust_carrier), "--trust-carrier-type", "filesystem",
                "--trust-roots", str(unknown_roots), "--json",
            ],
            cwd=root, env=negative_env, logs=logs, step="acquire-unknown-signature", expected_success=False,
        )
        diagnostic = (negative.stdout + "\n" + negative.stderr).lower()
        if not any(word in diagnostic for word in ("signature", "signer", "trust")):
            raise RuntimeFailure("unknown-signature acquisition failed without a trust/signature diagnostic")

    return {
        "status": "PASS",
        "reason": "strict provider acquisition, provider-down warm reuse, provider-acquired leased task execution, strict archive-only import/task execution, output validation, and unknown-signer rejection passed",
        "evidence": {
            "directory": str(evidence_dir),
            "archive": str(archive),
            "provider_execution_receipt": str(provider_receipt),
            "archive_execution_receipt": str(archive_receipt),
            "logs": str(logs),
            "trust_carrier": str(trust_carrier),
            "deployment_trust_roots": str(trust_roots),
            "provider_output": str(provider_output),
            "archive_output": str(archive_output),
        },
        "identity": {
            "rcc_version": rcc_version,
            "rcc_sha256": rcc_sha,
            "artifact": artifact,
            "environment_specification_digest": publish.get("specificationDigest"),
            "source_digest": source_before,
            "worker_platform": "linux_amd64",
            "provider": provider_url,
            "provider_server_pid": provider_process.pid,
            "provider_server_exit_code": provider_server_exit,
            "deployment_robo_home": str(consumer_home),
            "archive_robo_home": str(archive_home),
            "trust_policy": "strict-remote; detached filesystem carrier; deployment-owned roots",
            "archive_bytes": archive.stat().st_size,
            "outputs": {"provider_acquire": provider_hashes, "archive_only": archive_hashes},
            "execution_receipts": {"provider_acquire": provider_receipt_result, "archive_only": archive_receipt_result},
            "publish_result": publish,
            "provider_acquire_result": _acquire_summary(provider_acquire),
            "archive_acquire_result": _acquire_summary(archive_acquire),
            "warm_reuse_result": _acquire_summary(warm),
        },
    }


def run_mode(root: Path, evidence_dir: Path, rcc: str) -> dict[str, Any]:
    """Run C and return a serializable PASS/FAIL/SKIPPED receipt with evidence paths."""
    started = time.monotonic()
    try:
        result = _run_mode(Path(root), Path(evidence_dir), rcc)
    except PinnedRccUnavailable as exc:
        result = {"status": "SKIPPED", "reason": str(exc), "evidence": {"directory": str(Path(evidence_dir).expanduser().resolve())}, "identity": {}}
    except Exception as exc:
        result = {"status": "FAIL", "reason": f"{type(exc).__name__}: {exc}", "evidence": {"directory": str(Path(evidence_dir).expanduser().resolve())}, "identity": {}}
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    evidence_path = Path(evidence_dir).expanduser().resolve()
    try:
        evidence_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt_path = evidence_path / "c-result.json"
        receipt_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result.setdefault("evidence", {})["result"] = str(receipt_path)
        receipt_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--rcc", required=True)
    args = parser.parse_args()
    result = run_mode(args.root, args.evidence_dir, args.rcc)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
