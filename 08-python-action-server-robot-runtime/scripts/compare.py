"""Run real local comparisons. A blocked required mode returns a nonzero exit."""

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def summarize(modes, source):
    results = {name: modes.get(name, {"status": "SKIPPED", "reason": "not selected"})
               for name in "ABC"}
    results["D"] = {
        "status": "NOT IMPLEMENTED",
        "reason": ("Action Server robot artifact worker requires Actions #133/#134. "
                   "C does not prove D."),
        "issues": ["https://github.com/joshyorko/actions/issues/133",
                   "https://github.com/joshyorko/actions/issues/134"],
    }
    return {"schema_version": 1, "source_digest": source, "modes": results,
            "acceptance_passed": all(results[n]["status"] == "PASS" for n in "ABC")}


def render_report(report, directory):
    write_json(directory / "report.json", report)
    rows = []
    for name, result in report["modes"].items():
        detail = json.dumps(result, indent=2, sort_keys=True)
        rows.append(f"<tr><th>{name}</th><td>{html.escape(result['status'])}</td>"
                    f"<td><pre>{html.escape(detail)}</pre></td></tr>")
    document = (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Robot execution comparison</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}"
        "td,th{border:1px solid #aaa;padding:.6rem;vertical-align:top}"
        "table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
        "</style><h1>Robot execution comparison</h1>"
        f"<p>Source digest: {html.escape(report['source_digest'])}</p>"
        "<p>A: classic RCC. B: existing Action Server robot API. C: direct RCC signed artifact. "
        "D: future Action Server artifact worker. Durations describe these trials only.</p>"
        "<p>Primary CLI/API receipts and output files are retained beside this report. "
        "No speedup or production-readiness claim is made.</p>"
        "<table><tr><th>Mode</th><th>Status</th><th>Actual evidence</th></tr>"
        + "".join(rows) + "</table></html>\n")
    (directory / "report.html").write_text(document)


def command(args, *, cwd, env, prefix, timeout=1200):
    prefix.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with prefix.with_suffix(".stdout").open("w") as stdout, \
            prefix.with_suffix(".stderr").open("w") as stderr:
        try:
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=stdout,
                                       stderr=stderr, start_new_session=True)
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            stderr.write(f"\nCommand timed out after {timeout} seconds.\n")
            exit_code = 124
    record = {"command": list(map(str, args)), "exit_code": exit_code,
              "duration_seconds": round(time.monotonic() - started, 6),
              "stdout": str(prefix.with_suffix(".stdout")),
              "stderr": str(prefix.with_suffix(".stderr"))}
    write_json(prefix.with_suffix(".json"), record)
    return record


def classic(root, directory, rcc):
    from invoice_robot import source_digest, verify_outputs

    directory.mkdir(parents=True)
    source = directory / "source"
    source.mkdir()
    for name in ["robot.yaml", "conda.yaml", "tasks.py", "invoice_robot", "devdata",
                 ".gitignore"]:
        item = root / name
        if item.is_dir():
            shutil.copytree(item, source / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif item.exists():
            shutil.copy2(item, source / name)
    expected_source = source_digest(root)
    if source_digest(source) != expected_source:
        raise RuntimeError("classic source copy digest differs")
    env = os.environ.copy()
    env.update(ROBOCORP_HOME=str(directory / "home"), RCC_HOLOTREE_MODE="private")
    resolved = command([rcc, "ht", "vars", "-r", "robot.yaml", "--json"],
                       cwd=source, env=env, prefix=directory / "resolve")
    if resolved["exit_code"]:
        return {"status": "FAIL", "reason": "RCC environment resolution failed", "resolve": resolved}
    trials = []
    for name, fixture, task in [
        ("baseline", "invoices.json", "ValidateBatch"),
        ("repeat", "invoices.json", "ValidateBatch"),
        ("alternate", "invoices-alternate.json", "ValidateBatch"),
        ("failure", "invoices.json", "FailBatch"),
    ]:
        output = source / "output"
        if output.exists():
            shutil.rmtree(output)
        trial = directory / name
        trial.mkdir()
        env_file = trial / "env.json"
        write_json(env_file, {"INVOICE_INPUT": str(source / "devdata" / fixture)})
        run = command([rcc, "run", "-r", "robot.yaml", "-t", task,
                       "-e", str(env_file), "--silent"], cwd=source, env=env,
                      prefix=trial / "run")
        if output.exists():
            shutil.copytree(output, trial / "output")
        if task == "FailBatch":
            if run["exit_code"] == 0 or not (trial / "output" / "failure.json").exists():
                raise RuntimeError("classic failure task did not fail with durable evidence")
        else:
            if run["exit_code"]:
                raise RuntimeError(f"classic {name} failed: see {trial}")
            run["outputs"] = verify_outputs(trial / "output", source / "devdata" / fixture)
        run.update(name=name, fixture=fixture, task=task)
        trials.append(run)
    warm = command([rcc, "ht", "vars", "-r", "robot.yaml", "--json", "--no-build"],
                   cwd=source, env=env, prefix=directory / "reuse")
    if warm["exit_code"]:
        raise RuntimeError("classic no-build repeat environment check failed")
    return {"status": "PASS", "reason": "truth, repeat, alternate input and failure verified",
            "source_digest": expected_source, "environment_spec_sha256": hashlib.sha256(
                (source / "conda.yaml").read_bytes()).hexdigest(),
            "trust_policy": "classic local environment; no artifact signature claim",
            "worker": platform.node(), "resolve": resolved, "no_build_reuse": warm,
            "trials": trials}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", default="ABC", help="subset of ABC; D is always NOT IMPLEMENTED")
    parser.add_argument("--rcc", default="rcc")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--server-url", help="optional prestarted isolated local Action Server")
    args = parser.parse_args(argv)
    if not args.modes or set(args.modes) - set("ABC"):
        parser.error("--modes must be a nonempty subset of ABC")
    from invoice_robot import source_digest
    from scripts.setup_rcc import verify_rcc

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = (args.evidence or ROOT / "output" / "comparison" / stamp).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    # Pin enforcement happens before any workload or environment mutation.
    rcc = str(Path(shutil.which(args.rcc) or args.rcc).resolve())
    identity = verify_rcc(rcc)
    expected_source = source_digest(ROOT)
    modes = {}
    for name in dict.fromkeys(args.modes):
        started = time.monotonic()
        print(f"Running {name}; evidence {directory / name}", flush=True)
        try:
            if name == "A":
                result = classic(ROOT, directory / name, rcc)
            elif name == "B":
                from scripts.action_server import run_mode
                result = run_mode(ROOT, directory / name, rcc, server_url=args.server_url)
            else:
                from scripts.artifact_runtime import run_mode
                result = run_mode(ROOT, directory / name, rcc)
            if source_digest(ROOT) != expected_source:
                raise RuntimeError("reviewed source changed during execution")
            if result.get("source_digest", expected_source) != expected_source:
                raise RuntimeError("mode executed a different source digest")
        except Exception as exc:
            (directory / f"{name}-error.txt").write_text(traceback.format_exc())
            result = {"status": "FAIL", "reason": str(exc), "evidence": [f"{name}-error.txt"]}
        result["duration_seconds"] = round(time.monotonic() - started, 6)
        modes[name] = result
        print(f"{name}: {result['status']} {result.get('reason', '')}", flush=True)
        report = summarize(modes, expected_source)
        report.update(rcc=identity, worker=platform.node(), platform=platform.platform(),
                      environment_spec_sha256=hashlib.sha256(
                          (ROOT / "conda.yaml").read_bytes()).hexdigest(),
                      generated_at=datetime.now(timezone.utc).isoformat())
        render_report(report, directory)
    print(f"Report: {directory / 'report.html'}", flush=True)
    return 0 if all(modes[name]["status"] == "PASS" for name in args.modes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
