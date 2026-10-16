import csv
import json
import tempfile
import unittest
from pathlib import Path

from invoice_robot import source_digest, validate_batch, verify_outputs


ROOT = Path(__file__).resolve().parents[1]


class WorkloadTests(unittest.TestCase):
    def test_default_fixture_matches_checked_in_truth(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            validate_batch(ROOT / "devdata/invoices.json", output)

            evidence = verify_outputs(output, ROOT / "devdata/invoices.json")
            expected = json.loads((ROOT / "devdata/expected.json").read_text())
            report = json.loads((output / "report.json").read_text())
            with (output / "results.csv").open(newline="") as results_file:
                rows = list(csv.DictReader(results_file))
            for row in rows:
                row["amount_cents"] = int(row["amount_cents"])

            self.assertTrue(evidence["ground_truth_match"])
            self.assertEqual(report, expected["report"])
            self.assertEqual(rows, expected["rows"])

    def test_alternate_input_changes_both_canonical_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            validate_batch(ROOT / "devdata/invoices.json", output)
            original_report = (output / "report.json").read_bytes()
            original_csv = (output / "results.csv").read_bytes()

            validate_batch(ROOT / "devdata/invoices-alternate.json", output)
            evidence = verify_outputs(output, ROOT / "devdata/invoices-alternate.json")

            self.assertFalse(evidence["ground_truth_match"])
            self.assertNotEqual((output / "report.json").read_bytes(), original_report)
            self.assertNotEqual((output / "results.csv").read_bytes(), original_csv)

    def test_invalid_input_fails_and_writes_failure_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bad_input = root / "bad.json"
            output = root / "artifacts"
            bad_input.write_text('{"invoices": []}')

            with self.assertRaises(ValueError):
                validate_batch(bad_input, output)

            failure = json.loads((output / "failure.json").read_text())
            self.assertEqual(failure["status"], "FAIL")
            self.assertIn("at least one invoice", failure["error"])

    def test_source_digest_is_stable_and_tracks_source_changes(self):
        first = source_digest(ROOT)
        self.assertEqual(first, source_digest(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            copied = Path(temp_dir) / "template"
            import shutil

            shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns("__pycache__"))
            self.assertEqual(first, source_digest(copied))
            with (copied / "invoice_robot/validator.py").open("a") as source:
                source.write("\n# changed\n")
            self.assertNotEqual(first, source_digest(copied))


if __name__ == "__main__":
    unittest.main()
