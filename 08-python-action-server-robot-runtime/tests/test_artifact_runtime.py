from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("artifact_runtime", SCRIPT_ROOT / "scripts" / "artifact_runtime.py")
assert SPEC and SPEC.loader
artifact_runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artifact_runtime)
SETUP_SPEC = importlib.util.spec_from_file_location("setup_rcc", SCRIPT_ROOT / "scripts" / "setup_rcc.py")
assert SETUP_SPEC and SETUP_SPEC.loader
setup_rcc = importlib.util.module_from_spec(SETUP_SPEC)
SETUP_SPEC.loader.exec_module(setup_rcc)


class ArtifactRuntimeTests(unittest.TestCase):
    def test_lock_matches_qualified_release(self) -> None:
        lock = artifact_runtime._load_lock()
        self.assertEqual(lock["version"], "18.19.7")
        self.assertEqual(lock["sha256"], "f8e0394be7f47ee84d6f4cd734822b87b492a4d6102f977f6a18f29453703d48")
        self.assertEqual(setup_rcc.load_lock(), lock)

    def test_setup_verifier_rejects_a_non_pinned_executable_before_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "rcc"
            binary.write_text("not RCC", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                setup_rcc.verify_rcc(binary)

    def test_artifact_identity_requires_canonical_lowercase_digest(self) -> None:
        digest = "sha256:" + "a" * 64
        self.assertEqual(artifact_runtime._artifact_digest({"artifact": digest}), digest)
        for invalid in ("sha256:" + "A" * 64, "sha256:" + "a" * 63, "not-an-artifact"):
            with self.subTest(invalid=invalid), self.assertRaises(artifact_runtime.RuntimeFailure):
                artifact_runtime._artifact_digest({"artifact": invalid})

    def test_private_robo_env_overrides_shared_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = artifact_runtime._private_robo_env(Path(temp) / "private-home")
        self.assertEqual(env["RCC_HOLOTREE_MODE"], "private")
        self.assertTrue(env["ROBOCORP_HOME"].endswith("private-home"))

    def test_workload_helpers_must_resolve_inside_selected_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "invoice_robot"
            package.mkdir()
            (package / "__init__.py").write_text(
                "def source_digest(root): return 'sha256:test'\n"
                "def verify_outputs(output_dir, input_path): return {'ground_truth_match': True}\n",
                encoding="utf-8",
            )
            source_digest, verify_outputs = artifact_runtime._source_api(root)
            self.assertEqual(source_digest(root), "sha256:test")
            self.assertEqual(verify_outputs(root, root / "input.json"), {"ground_truth_match": True})

    def test_task_bootstrap_runs_declared_robocorp_task_with_explicit_paths(self) -> None:
        source = Path("/source/template")
        input_path = Path("/fixtures/invoices.json")
        output = Path("/evidence/output")
        command = artifact_runtime._task_bootstrap(source, input_path, output)
        self.assertEqual(command[0:2], ["python", "-c"])
        self.assertIn("runpy.run_module('robocorp.tasks'", command[2])
        self.assertIn("validate_batch_task", command[2])
        self.assertIn("INVOICE_INPUT", command[2])
        self.assertIn("ROBOT_ARTIFACTS", command[2])
        self.assertEqual(command[3:], [str(source), str(source / "tasks.py"), str(input_path), str(output)])

    def test_execution_receipt_requires_a_strict_completed_lease(self) -> None:
        artifact = "sha256:" + "a" * 64
        receipt = {
            "artifactDigest": artifact,
            "status": "completed",
            "exitCode": 0,
            "leaseId": "lease-1",
            "cacheHit": "local-materialization",
            "verification": {"valid": True, "policyMode": "strict-remote", "keyID": "signer"},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "receipt.json"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            summary = artifact_runtime._validate_exec_receipt(path, artifact)
            self.assertEqual(summary["exitCode"], 0)
            self.assertEqual(summary["verification"]["policyMode"], "strict-remote")
            receipt["verification"]["policyMode"] = "permissive-local"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(artifact_runtime.RuntimeFailure, "strict-remote"):
                artifact_runtime._validate_exec_receipt(path, artifact)

    def test_unknown_signature_roots_use_a_distinct_trust_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = Path(temp) / "roots.json"
            artifact_runtime._write_unknown_roots(roots)
            value = json.loads(roots.read_text(encoding="utf-8"))
            self.assertEqual(list(value), ["untrusted-template-08-key"])
            self.assertEqual(len(value["untrusted-template-08-key"]), 43)
            self.assertNotIn(" ", roots.read_text(encoding="utf-8"))
            self.assertFalse(roots.read_bytes().endswith(b"\n"))

    def test_trust_roots_are_canonical_compact_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            roots = Path(temp) / "roots.json"
            artifact_runtime._write_trust_roots(roots, "release-key", b"\x02" * 32)
            self.assertEqual(roots.read_text(encoding="utf-8"), '{"release-key":"AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI"}')


if __name__ == "__main__":
    unittest.main()
