"""Standard-library invoice validation core and reproducible artifact checks."""

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any


DEFAULT_FIXTURE = Path("devdata/invoices.json")
EXPECTED_FIXTURE = Path("devdata/expected.json")
SOURCE_PATHS = (
    "robot.yaml",
    "conda.yaml",
    "tasks.py",
    "invoice_robot/__init__.py",
    "invoice_robot/__main__.py",
    "invoice_robot/validator.py",
    "devdata/invoices.json",
    "devdata/invoices-alternate.json",
)
CSV_FIELDS = ("invoice_id", "vendor", "amount_cents", "currency", "classification")


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_invoices(input_path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read invoice input: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("invoices"), list):
        raise ValueError("input must be an object containing an invoices array")
    invoices = data["invoices"]
    if not invoices:
        raise ValueError("invoice batch must contain at least one invoice")

    seen_ids: set[str] = set()
    for index, invoice in enumerate(invoices):
        if not isinstance(invoice, dict):
            raise ValueError(f"invoice {index} must be an object")
        invoice_id = invoice.get("invoice_id")
        vendor = invoice.get("vendor")
        amount_cents = invoice.get("amount_cents")
        currency = invoice.get("currency")
        if not isinstance(invoice_id, str) or not invoice_id.strip():
            raise ValueError(f"invoice {index} requires a non-empty invoice_id")
        if invoice_id in seen_ids:
            raise ValueError(f"duplicate invoice_id: {invoice_id}")
        seen_ids.add(invoice_id)
        if not isinstance(vendor, str) or not vendor.strip():
            raise ValueError(f"invoice {invoice_id} requires a non-empty vendor")
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents <= 0:
            raise ValueError(f"invoice {invoice_id} amount_cents must be a positive integer")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
            raise ValueError(f"invoice {invoice_id} currency must be a three-letter code")
    return invoices


def _render(invoices: list[dict[str, Any]]) -> tuple[dict[str, Any], bytes]:
    rows = []
    for invoice in sorted(invoices, key=lambda item: item["invoice_id"]):
        classification = "review" if invoice["amount_cents"] >= 100_000 else "ready"
        rows.append(
            {
                "invoice_id": invoice["invoice_id"],
                "vendor": invoice["vendor"].strip(),
                "amount_cents": invoice["amount_cents"],
                "currency": invoice["currency"].upper(),
                "classification": classification,
            }
        )
    counts = {"ready": 0, "review": 0}
    totals = {"ready_cents": 0, "review_cents": 0}
    for row in rows:
        key = row["classification"]
        counts[key] += 1
        totals[f"{key}_cents"] += row["amount_cents"]
    report = {
        "invoice_count": len(rows),
        "classification_counts": counts,
        "classification_totals_cents": totals,
        "total_cents": sum(row["amount_cents"] for row in rows),
    }
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return report, stream.getvalue().encode("utf-8")


def _write_failure(output_dir: Path, message: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").unlink(missing_ok=True)
    (output_dir / "results.csv").unlink(missing_ok=True)
    (output_dir / "failure.json").write_bytes(
        _canonical_json({"status": "FAIL", "error": message})
    )


def validate_batch(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    force_failure: bool = False,
) -> dict[str, Any]:
    """Validate and write deterministic report.json and results.csv artifacts."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    try:
        if force_failure:
            raise RuntimeError("deliberate failure requested")
        report, csv_bytes = _render(_read_invoices(input_path))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.json").write_bytes(_canonical_json(report))
        (output_dir / "results.csv").write_bytes(csv_bytes)
        (output_dir / "failure.json").unlink(missing_ok=True)
        return report
    except (OSError, ValueError, RuntimeError) as error:
        _write_failure(output_dir, str(error))
        raise


def verify_outputs(output_dir: str | Path, input_path: str | Path) -> dict[str, Any]:
    """Verify exact expected artifacts; raise if they differ from the input-derived result."""
    output_dir = Path(output_dir)
    input_path = Path(input_path)
    report, csv_bytes = _render(_read_invoices(input_path))
    expected_report = _canonical_json(report)
    report_path = output_dir / "report.json"
    csv_path = output_dir / "results.csv"
    actual_report = report_path.read_bytes()
    actual_csv = csv_path.read_bytes()
    if actual_report != expected_report:
        raise ValueError("report.json does not match the canonical invoice report")
    if actual_csv != csv_bytes:
        raise ValueError("results.csv does not match the canonical invoice rows")

    default_input = input_path.resolve() == (Path(__file__).resolve().parents[1] / DEFAULT_FIXTURE).resolve()
    ground_truth_match = False
    if default_input:
        expected = json.loads((input_path.parent / EXPECTED_FIXTURE.name).read_text(encoding="utf-8"))
        ground_truth_match = (
            actual_report == _canonical_json(expected["report"])
            and actual_csv == _csv_for_expected(expected["rows"])
        )
        if not ground_truth_match:
            raise ValueError("default fixture outputs do not match checked-in ground truth")
    return {
        "report_sha256": hashlib.sha256(actual_report).hexdigest(),
        "results_sha256": hashlib.sha256(actual_csv).hexdigest(),
        "ground_truth_match": ground_truth_match,
        "invoice_count": report["invoice_count"],
    }


def _csv_for_expected(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def source_digest(root: str | Path) -> str:
    """Hash the explicit ordered runtime/config/fixture allowlist under template root."""
    root = Path(root)
    if not (root / "robot.yaml").is_file() and (root / "08-python-action-server-robot-runtime").is_dir():
        root = root / "08-python-action-server-robot-runtime"
    digest = hashlib.sha256()
    for relative_path in SOURCE_PATHS:
        path = root / relative_path
        if not path.is_file():
            raise FileNotFoundError(f"source digest input is missing: {relative_path}")
        content = path.read_bytes()
        digest.update(relative_path.encode("utf-8") + b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
