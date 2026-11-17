import json
from pathlib import Path
import tempfile
import unittest

from scripts.compare import render_report, summarize


class ComparisonTests(unittest.TestCase):
    def test_blocker_cannot_become_green(self):
        report = summarize({"A": {"status": "PASS"}, "B": {
            "status": "BLOCKED", "reason": "outputs missing"}}, "sha256:abc")
        self.assertFalse(report["acceptance_passed"])
        self.assertEqual(report["modes"]["D"]["status"], "NOT IMPLEMENTED")
        self.assertNotEqual(report["modes"]["C"]["status"], "PASS")

    def test_html_escapes_evidence_and_contains_primary_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            report = summarize({"A": {"status": "FAIL", "reason": "<unsafe>",
                                      "evidence": ["A/run.json"]}}, "sha256:abc")
            render_report(report, target)
            html = (target / "report.html").read_text()
            self.assertIn("&lt;unsafe&gt;", html)
            self.assertNotIn("<unsafe>", html)
            self.assertIn("A/run.json", html)
            self.assertEqual(json.loads((target / "report.json").read_text()), report)


if __name__ == "__main__":
    unittest.main()
