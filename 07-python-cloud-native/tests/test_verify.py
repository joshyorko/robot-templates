"""Mutation tests use labelled fabricated RCC/container records, never live proof."""

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import zipfile

from cleanroom.recorder import Recorder
from cleanroom.verify import _verify_archive, _verify_carrier, _verify_carrier_binding, _verify_pixels, main, sha256, verify
from redactor.corpus import generate
from redactor.integrity import source_digest


HAS_PDF = importlib.util.find_spec("pypdfium2") is not None and importlib.util.find_spec("PIL") is not None


class VerifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        folder = Path(cls.keys.name)
        cls.addClassCleanup(cls.keys.cleanup)
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(folder / "key.pem")], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(folder / "key.pem"), "-pubout", "-outform", "DER", "-out", str(folder / "public.der")], check=True, capture_output=True)
        cls.public = base64.b64encode((folder / "public.der").read_bytes()[-32:]).decode().rstrip("=")
        identity = b'{"mediaType":"unit-test-fixture","schemaVersion":1,"platform":{"rccPlatform":"linux_amd64"}}'
        cls.environment_digest = "sha256:" + hashlib.sha256(identity).hexdigest()
        cls.archive_manifest = identity.replace(b',"platform"', b',"artifactDigest":"' + cls.environment_digest.encode() + b'","platform"')
        (folder / "message").write_bytes((cls.environment_digest + "\x00fixture-key\x00Ed25519\x00\x00").encode())
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(folder / "key.pem"), "-rawin", "-in", str(folder / "message"), "-out", str(folder / "signature")], check=True, capture_output=True)
        cls.signature = base64.b64encode((folder / "signature").read_bytes()).decode().rstrip("=")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write("source/redactor/__init__.py", "# Explicit unit-test source fixture.\n")
        (self.root / "source/cleanroom").mkdir()
        self.write("source/clean-room-robot.yaml", "# Unit-test source fixture\n")
        self.write("source/clean-room-environment.yaml", "# Unit-test source fixture\n")
        generate(self.root / "corpus", 1)
        self.write("tools/rcc", "UNIT TEST ONLY: not the released RCC binary\n")
        pin = sha256(self.root / "tools/rcc")
        self.pin = patch("cleanroom.verify.RCC_SHA256", pin)
        self.pin.start()
        self.addCleanup(self.pin.stop)
        with zipfile.ZipFile(self.root / "environment.rcca", "w") as archive:
            archive.writestr("rcc-environment/manifest.json", self.archive_manifest)
        digest = self.environment_digest
        self.json("roots.json", {"fixture-key": self.public})
        prefix = "carrier/sha256/" + digest.split(":", 1)[1] + "/"
        for kind, data in {
            "provenance": {"platform": "linux_amd64"}, "sbom": {"fixture": True},
            "revocations": {"fetchedAt": datetime.now(timezone.utc).isoformat(), "revocations": []},
            "signature": {"signatures": [{"artifactDigest": digest, "keyID": "fixture-key", "algorithm": "Ed25519", "signature": self.signature}]},
        }.items():
            self.json(prefix + kind + ".json", dict(data, artifactDigest=digest))
        source = source_digest(self.root / "source")
        self.manifest = {
            "schema": 1, "run_id": "unit-test-fabricated-evidence",
            "environment_digest": digest, "source_digest": source,
            "rcc_sha256": pin, "rcc_binary": "tools/rcc", "source": "source", "corpus": "corpus",
            "corpus_sha256": sha256(self.root / "corpus/corpus.json"),
            "roots_sha256": sha256(self.root / "roots.json"),
            "archive": {"path": "environment.rcca", "bytes": (self.root / "environment.rcca").stat().st_size,
                        "members": 1, "sha256": sha256(self.root / "environment.rcca")},
            "carrier": {"sealed_at": datetime.now(timezone.utc).isoformat(),
                        "files": {path.relative_to(self.root).as_posix(): sha256(path) for path in (self.root / "carrier").rglob("*.json")}},
            "network": "evidence/network.json", "workers": [],
            "image_id": "sha256:" + "c" * 64,
        }
        self.json("evidence/network.json", [{"Name": "fixture-internal", "Internal": True,
                                             "Labels": {"cleanroom.run": self.manifest["run_id"]}}])
        self.json("evidence/base-image.json", [{"Id": self.manifest["image_id"], "Architecture": "amd64", "Os": "linux"}])
        self.json("evidence/base-glibc.command.json", {"exit_code": 0, "timed_out": False})
        self.json("evidence/edge-inspect.json", [{"HostConfig": {"NetworkMode": "fixture-internal", "Privileged": False},
                  "Config": {"Image": self.manifest["image_id"], "Labels": {"cleanroom.run": self.manifest["run_id"]},
                             "Cmd": ["UNIT TEST FIXTURE 127.0.0.1:8123"]}}])
        self.json("evidence/edge-boundary.json", {"https": True, "unauthenticated": 401, "admin": 403, "trust_put": 403, "object_put": 403})
        self.json("evidence/put-content-length.json", {"put_status": 201, "content_length": 31})
        for number in range(3):
            name = f"fixture-{number}"
            worker = {"name": name, "offline": number == 2,
                      "acquire": f"evidence/{name}-acquire.json",
                      "receipt": f"workers/{name}/receipt.json",
                      "inspect": f"evidence/{name}-inspect.json",
                      "output": f"workers/{name}/output",
                      "results": f"evidence/{name}-results.json",
                      "network_probe": f"evidence/{name}-network-probe.json"}
            self.json(worker["network_probe"], {"external_reachable": False, "provider_loopback_reachable": False})
            decision = {"artifactDigest": digest, "verification": {
                "valid": True, "code": "valid", "policyMode": "strict-remote",
                "artifactDigest": digest, "verifiedAt": datetime.now(timezone.utc).isoformat()}}
            self.json(worker["acquire"], decision)
            self.json(worker["receipt"], dict(decision, status="completed", exitCode=0, leaseId="fixture-lease-" + name))
            self.json(worker["inspect"], [{
                "HostConfig": {"NetworkMode": "none" if worker["offline"] else "fixture-internal",
                               "Privileged": False, "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                               "SecurityOpt": ["no-new-privileges"]},
                "Config": {"Env": ["ROBOCORP_HOME=/rcc-home", "CLEAN_ROOM_SOURCE_DIGEST=" + source],
                           "Labels": {"cleanroom.run": self.manifest["run_id"]}, "Image": self.manifest["image_id"]},
                "Mounts": [{"Destination": "/src", "Source": str(self.root / "source"), "RW": False},
                           {"Destination": "/carrier", "Source": str(self.root / "carrier"), "RW": False},
                           {"Destination": "/rcc-home", "Source": str(self.root / "workers" / name / "home"), "RW": True}]
                          + [{"Destination": destination, "Source": str(self.root / {
                              "/roots.json": "roots.json", "/corpus": "corpus", "/ca.pem": "ca.pem",
                              "/usr/local/bin/rcc": "tools/rcc", "/environment.rcca": "environment.rcca", "/token": "secrets/token"}[destination]), "RW": False}
                             for destination in ("/roots.json", "/corpus", "/ca.pem", "/usr/local/bin/rcc", "/environment.rcca" if worker["offline"] else "/token")]
                          + [{"Destination": destination, "Source": str(self.root / "workers" / name / ("output" if destination.endswith("output") else "")), "RW": True}
                             for destination in ("/work", "/work/output")]}])
            (self.root / worker["output"]).mkdir(parents=True)
            self.manifest["workers"].append(worker)
        self.json("closure.json", {"status": "completed", "fixture": True})
        Recorder(self.root).event("closure", evidence="closure.json")
        self.save()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def json(self, name, value):
        self.write(name, json.dumps(value))

    def load(self, name):
        return json.loads((self.root / name).read_text())

    def save(self):
        self.json("run.json", self.manifest)

    def fabricated_workload(self):
        """Construct malformed-PNG negative fixtures, never successful pixel proof."""
        document = self.load("corpus/corpus.json")["documents"][0]
        for worker in self.manifest["workers"]:
            output = self.root / worker["output"]
            (output / "doc-0001.png").write_bytes(b"INTENTIONALLY INVALID PNG UNIT TEST FIXTURE")
            (output / "doc-0001.txt").write_text(document["expected_text"])
            evidence = {"id": "doc-0001", "mode": "digital-pdf-not-ocr", "redacted_path": "doc-0001.png",
                        "text_path": "doc-0001.txt", "text": document["expected_text"],
                        "sha256": sha256(output / "doc-0001.png"), "text_sha256": sha256(output / "doc-0001.txt"),
                        "redactions": [{"kind": label["kind"], "rectangle": [0, 0, 1, 1]} for label in document["truth"]]}
            self.json(worker["output"] + "/doc-0001.json", evidence)
            self.json(worker["results"], [{"round": round_number, "document": "doc-0001", "response": {
                "content": [{"type": "text", "text": json.dumps(evidence)}], "isError": False}} for round_number in (0, 1)])

    def real_pdf_workload(self):
        from redactor.engine import redact_document

        for worker in self.manifest["workers"]:
            results = []
            for round_number in (0, 1):
                evidence = redact_document(self.root / "corpus", self.root / worker["output"], "doc-0001")
                results.append({"round": round_number, "document": "doc-0001", "response": {
                    "content": [{"type": "text", "text": json.dumps(evidence)}], "isError": False}})
            self.json(worker["results"], results)

    def change_evidence(self, worker, modify):
        evidence_path = worker["output"] + "/doc-0001.json"
        evidence = self.load(evidence_path)
        modify(evidence)
        self.json(evidence_path, evidence)
        results = self.load(worker["results"])
        for result in results:
            result["response"]["content"][0]["text"] = json.dumps(evidence)
        self.json(worker["results"], results)

    def rejected(self, message):
        with self.assertRaisesRegex(ValueError, message):
            verify(self.root)

    def test_archive_and_carrier_real_file_hashes(self):
        _verify_archive(self.root, self.manifest["archive"])
        _verify_carrier(self.root, self.manifest["carrier"])
        _verify_carrier_binding(self.root, self.manifest["carrier"], self.environment_digest)

    def test_artifact_signature_independent_verification(self):
        name = "carrier/sha256/" + self.environment_digest.split(":", 1)[1] + "/signature.json"
        attachment = self.load(name)
        attachment["signatures"][0]["signature"] = base64.b64encode(b"X" * 64).decode()
        self.json(name, attachment)
        self.manifest["carrier"]["files"][name] = sha256(self.root / name)
        self.save()
        self.rejected("No independently verified trusted artifact signature")

    def test_root_key_replacement(self):
        self.json("roots.json", {"rogue-identity": self.public})
        self.rejected("Site trust roots hash mismatch")

    def test_untrusted_key_even_with_roots_hash_updated(self):
        self.json("roots.json", {"rogue-identity": self.public})
        self.manifest["roots_sha256"] = sha256(self.root / "roots.json")
        self.save()
        self.rejected("No independently verified trusted artifact signature")

    def test_revoked_signer(self):
        name = "carrier/sha256/" + self.environment_digest.split(":", 1)[1] + "/revocations.json"
        attachment = self.load(name)
        attachment["revocations"] = [{"keyIDs": ["fixture-key"], "updatedAt": datetime.now(timezone.utc).isoformat()}]
        self.json(name, attachment)
        self.manifest["carrier"]["files"][name] = sha256(self.root / name)
        self.save()
        self.rejected("All trusted signing keys are revoked")

    def test_binary_mutation(self):
        self.write("tools/rcc", "Not the pinned RCC bytes")
        self.rejected("RCC v18.19.6 binary checksum mismatch")

    def test_remaining_secrets(self):
        self.write("secrets/token", "unit-test-placeholder")
        self.rejected("secrets have not been removed")

    def test_no_manifest_is_not_success(self):
        (self.root / "run.json").unlink()
        self.rejected("Missing evidence: run.json")

    def test_source_mutation(self):
        self.write("source/redactor/__init__.py", "# Modified source.\n")
        self.rejected("Source digest mismatch")

    def test_untracked_python_startup_hook(self):
        self.write("source/sitecustomize.py", "raise RuntimeError('unit test startup hook')\n")
        self.rejected("Staged source top-level membership mismatch")

    def test_truth_manifest_mutation(self):
        corpus = self.load("corpus/corpus.json")
        corpus["documents"][0]["expected_text"] = "false expected output"
        self.json("corpus/corpus.json", corpus)
        self.rejected("Corpus truth manifest hash mismatch")

    def test_archive_mutation(self):
        with (self.root / "environment.rcca").open("r+b") as stream:
            stream.seek(20)
            stream.write(b"!")
        self.rejected("Archive hash mismatch")

    def test_archive_claimed_identity_must_match_manifest_bytes(self):
        content = self.archive_manifest.replace(b"unit-test-fixture", b"altered-fixture")
        with zipfile.ZipFile(self.root / "environment.rcca", "w") as archive:
            archive.writestr("rcc-environment/manifest.json", content)
        self.manifest["archive"]["bytes"] = (self.root / "environment.rcca").stat().st_size
        self.manifest["archive"]["sha256"] = sha256(self.root / "environment.rcca")
        self.save()
        self.rejected("Archive manifest identity hash mismatch")

    def test_archive_size_and_member_count(self):
        for key in ("bytes", "members"):
            with self.subTest(key=key):
                old = self.manifest["archive"][key]
                self.manifest["archive"][key] += 1
                self.save()
                self.rejected("Archive (size|member count) mismatch")
                self.manifest["archive"][key] = old

    def test_archive_uncompressed_limit(self):
        with zipfile.ZipFile(self.root / "bomb.zip", "w", compression=zipfile.ZIP_DEFLATED) as stream:
            # ZIP metadata is patched below without allocating a 256 MiB payload.
            stream.writestr("payload", b"small")
        data = bytearray((self.root / "bomb.zip").read_bytes())
        central = data.index(b"PK\x01\x02")
        data[central + 24:central + 28] = (256 * 1024 * 1024 + 1).to_bytes(4, "little")
        (self.root / "bomb.zip").write_bytes(data)
        archive = {"path": "bomb.zip", "bytes": len(data), "members": 1,
                   "sha256": hashlib.sha256(data).hexdigest()}
        with self.assertRaisesRegex(ValueError, "uncompressed content exceeds"):
            _verify_archive(self.root, archive)

    def test_carrier_mutation(self):
        name = next(iter(self.manifest["carrier"]["files"]))
        self.write(name, "Changed trust metadata\n")
        self.rejected("Detached carrier modified")

    def test_carrier_expiry_and_future(self):
        for offset in (timedelta(hours=-25), timedelta(minutes=5)):
            with self.subTest(offset=offset):
                self.manifest["carrier"]["sealed_at"] = (datetime.now(timezone.utc) + offset).isoformat()
                self.save()
                self.rejected("future-dated or older than 24 hours")

    def test_fresh_seal_cannot_hide_stale_revocation_snapshot(self):
        name = "carrier/sha256/" + self.environment_digest.split(":", 1)[1] + "/revocations.json"
        attachment = self.load(name)
        attachment["fetchedAt"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self.json(name, attachment)
        self.manifest["carrier"]["files"][name] = sha256(self.root / name)
        self.save()
        self.rejected("Revocation snapshot is stale or future-dated")

    def test_no_carrier_is_not_success(self):
        self.manifest["carrier"]["files"] = {}
        self.save()
        self.rejected("Detached trusted carrier is missing")

    def test_unsigned_or_wrong_digest_rejected(self):
        path = self.manifest["workers"][0]["acquire"]
        original = self.load(path)
        for key, value, error in (("verification", {}, "strict signed verification"),
                                  ("artifactDigest", "sha256:" + "f" * 64, "environment digest mismatch")):
            with self.subTest(key=key):
                mutated = dict(original, **{key: value})
                self.json(path, mutated)
                self.rejected(error)

    def test_strict_rejection_cannot_be_promoted_by_success_closure(self):
        path = self.manifest["workers"][0]["receipt"]
        receipt = self.load(path)
        receipt["verification"] = {"valid": False, "code": "revoked", "policyMode": "strict-remote"}
        self.json(path, receipt)
        self.rejected("strict signed verification")

    def test_active_receipt_is_not_complete(self):
        path = self.manifest["workers"][0]["receipt"]
        receipt = self.load(path)
        receipt["status"] = "active"
        self.json(path, receipt)
        self.rejected("execution has no successful closure")

    def test_nonzero_exit(self):
        path = self.manifest["workers"][0]["receipt"]
        receipt = self.load(path)
        receipt["exitCode"] = 1
        self.json(path, receipt)
        self.rejected("execution has no successful closure")

    def test_missing_receipt(self):
        (self.root / self.manifest["workers"][0]["receipt"]).unlink()
        self.rejected("Missing evidence.*receipt")

    def test_external_network(self):
        self.json("evidence/network.json", [{"Name": "fixture-internal", "Internal": False}])
        self.rejected("network is not isolated")

    def test_worker_network_probe_observed_egress(self):
        self.json(self.manifest["workers"][0]["network_probe"], {"external_reachable": True, "provider_loopback_reachable": False})
        self.rejected("network probe lacks isolation proof")

    def test_https_edge_boundary_probes(self):
        original = self.load("evidence/edge-boundary.json")
        for name in ("https", "unauthenticated", "admin", "trust_put", "object_put"):
            with self.subTest(boundary=name):
                self.json("evidence/edge-boundary.json", dict(original, **{name: 200 if name != "https" else False}))
                self.rejected("HTTPS edge boundary probes failed")

    def test_edge_public_port_rejected(self):
        edge = self.load("evidence/edge-inspect.json")
        edge[0]["HostConfig"]["PortBindings"] = {"8443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8443"}]}
        self.json("evidence/edge-inspect.json", edge)
        self.rejected("published-port boundary failed")

    def test_worker_wrong_image_rejected(self):
        path = self.manifest["workers"][0]["inspect"]
        inspected = self.load(path)
        inspected[0]["Config"]["Image"] = "mutable:tag"
        self.json(path, inspected)
        self.rejected("Worker image identity mismatch")

    def test_glibc_failed_rejected(self):
        self.json("evidence/base-glibc.command.json", {"exit_code": 1, "timed_out": False})
        self.rejected("glibc worker runtime check failed")

    def test_offline_worker_cannot_have_network(self):
        path = self.manifest["workers"][2]["inspect"]
        inspected = self.load(path)
        inspected[0]["HostConfig"]["NetworkMode"] = "fixture-internal"
        self.json(path, inspected)
        self.rejected("wrong container network")

    def test_unsafe_container_mutations(self):
        path = self.manifest["workers"][0]["inspect"]
        original = self.load(path)
        for key, value, message in (("Privileged", True, "privileged worker"),
                                    ("CapAdd", ["SYS_ADMIN"], "added container capabilities"),
                                    ("NetworkMode", "host", "wrong container network"),
                                    ("PidMode", "host", "host namespace shared")):
            with self.subTest(key=key):
                inspected = json.loads(json.dumps(original))
                inspected[0]["HostConfig"][key] = value
                self.json(path, inspected)
                self.rejected(message)

    def test_socket_and_rw_source_mount_rejected(self):
        path = self.manifest["workers"][0]["inspect"]
        inspected = self.load(path)
        inspected[0]["Mounts"].append({"Source": "/var/run/docker.sock", "Destination": "/socket", "RW": False})
        self.json(path, inspected)
        self.rejected("engine socket")
        inspected[0]["Mounts"].pop()
        inspected[0]["Mounts"][0]["RW"] = True
        self.json(path, inspected)
        self.rejected("mount write permissions mismatch")

    def test_shared_worker_home(self):
        path = self.manifest["workers"][1]["inspect"]
        inspected = self.load(path)
        inspected[0]["Mounts"][2]["Source"] = str(self.root / "workers/fixture-0/home")
        self.json(path, inspected)
        self.rejected("mount source mismatch|RCC homes are shared")

    def test_worker_source_verification_env_required(self):
        path = self.manifest["workers"][0]["inspect"]
        inspected = self.load(path)
        inspected[0]["Config"]["Env"] = ["ROBOCORP_HOME=/rcc-home"]
        self.json(path, inspected)
        self.rejected("verified source identity is absent")

    def test_container_identity_required(self):
        path = self.manifest["workers"][0]["inspect"]
        inspected = self.load(path)
        inspected[0]["Config"]["Labels"]["cleanroom.run"] = "other-run"
        self.json(path, inspected)
        self.rejected("does not belong to this run")

    def test_event_mutation(self):
        path = self.root / "events.jsonl"
        path.write_text(path.read_text().replace('"kind":"closure"', '"kind":"forged"'))
        self.rejected("Invalid event")

    def test_partial_event_and_incomplete_closure(self):
        path = self.root / "events.jsonl"
        original = path.read_bytes()
        path.write_bytes(original.rstrip(b"\n"))
        self.rejected("Incomplete event")
        path.write_bytes(original)
        self.json("closure.json", {"status": "active"})
        self.rejected("Run is active, blocked, or failed")

    def test_path_escape(self):
        self.manifest["source"] = "../source"
        self.save()
        self.rejected("escapes run root")

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as other:
            (self.root / "outside").symlink_to(other, target_is_directory=True)
            self.manifest["source"] = "outside"
            self.save()
            self.rejected("escapes run root")

    def test_missing_pixel_dependencies_fail_closed(self):
        original_import = __import__

        def imports(name, *args, **kwargs):
            if name == "pypdfium2":
                raise ImportError("unit-test dependency denial")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=imports):
            with self.assertRaisesRegex(ValueError, "Pixel verification blocked"):
                _verify_pixels(Path("unused"), Path("unused"), [], [])

    def test_cli_nonzero_on_incomplete(self):
        with patch("sys.stderr"):
            self.assertEqual(main(["--root", str(self.root)]), 1)

    def test_missing_mcp_round(self):
        self.fabricated_workload()
        worker = self.manifest["workers"][0]
        self.json(worker["results"], self.load(worker["results"])[:1])
        self.rejected("Missing MCP rounds or documents")

    def test_duplicate_mcp_round(self):
        self.fabricated_workload()
        worker = self.manifest["workers"][0]
        results = self.load(worker["results"])
        results[1]["round"] = 0
        self.json(worker["results"], results)
        self.rejected("Duplicate or missing MCP work")

    def test_mcp_error(self):
        self.fabricated_workload()
        worker = self.manifest["workers"][0]
        results = self.load(worker["results"])
        results[0]["response"]["isError"] = True
        self.json(worker["results"], results)
        self.rejected("MCP tool returned an error")

    def test_output_artifact_mutation(self):
        self.fabricated_workload()
        worker = self.manifest["workers"][0]
        self.write(worker["output"] + "/doc-0001.png", "mutated output")
        self.rejected("Redaction output hash mismatch")

    def test_self_consistent_wrong_redaction_text(self):
        self.fabricated_workload()
        worker = self.manifest["workers"][0]
        path = self.root / worker["output"] / "doc-0001.txt"
        path.write_text("Every worker could agree on this wrong answer.\n")
        self.change_evidence(worker, lambda evidence: evidence.update(text=path.read_text(), text_sha256=sha256(path)))
        self.rejected("Truth redacted text mismatch")

    def test_missing_truth_redaction(self):
        self.fabricated_workload()
        worker = self.manifest["workers"][0]
        self.change_evidence(worker, lambda evidence: evidence["redactions"].pop())
        self.rejected("Redaction coverage mismatch")

    def test_saved_output_evidence_disagrees_with_mcp(self):
        self.fabricated_workload()
        worker = self.manifest["workers"][0]
        self.json(worker["output"] + "/doc-0001.json", {"forged": True})
        self.rejected("Saved and MCP redaction evidence differ")

    @unittest.skipUnless(HAS_PDF, "requires pinned PDFium/Pillow; no pixel verification substitute")
    def test_real_pdf_complete_fixture_and_preclosure_verification(self):
        self.real_pdf_workload()
        self.assertEqual(verify(self.root)["status"], "verified")
        (self.root / "closure.json").unlink()
        self.assertEqual(verify(self.root, require_closure=False)["status"], "verified")
        self.rejected("Missing evidence: closure.json")

    @unittest.skipUnless(HAS_PDF, "requires pinned PDFium/Pillow; no pixel verification substitute")
    def test_visible_sensitive_pixels_with_updated_output_hash_rejected(self):
        from PIL import Image

        self.real_pdf_workload()
        worker = self.manifest["workers"][0]
        evidence = self.load(worker["output"] + "/doc-0001.json")
        image_path = self.root / worker["output"] / evidence["redacted_path"]
        left, top, right, bottom = evidence["redactions"][0]["rectangle"]
        with Image.open(image_path) as opened:
            image = opened.copy()
        image.putpixel(((left + right) // 2, (top + bottom) // 2), (255, 255, 255))
        image.save(image_path)
        image.close()
        self.change_evidence(worker, lambda item: item.update(sha256=sha256(image_path)))
        self.rejected("Sensitive pixels remain visible")

    @unittest.skipUnless(HAS_PDF, "requires pinned PDFium/Pillow; no pixel verification substitute")
    def test_safe_pixels_modified_with_updated_hash_rejected(self):
        from PIL import Image

        self.real_pdf_workload()
        worker = self.manifest["workers"][0]
        image_path = self.root / worker["output"] / "doc-0001.png"
        with Image.open(image_path) as opened:
            image = opened.copy()
        image.putpixel((0, 0), (255, 0, 0))
        image.save(image_path)
        image.close()
        self.change_evidence(worker, lambda item: item.update(sha256=sha256(image_path)))
        self.rejected("Safe pixels modified")

    @unittest.skipUnless(HAS_PDF, "requires pinned PDFium/Pillow; no pixel verification substitute")
    def test_identical_pixels_but_nondeterministic_bytes_rejected(self):
        from PIL import Image

        self.real_pdf_workload()
        worker = self.manifest["workers"][-1]
        image_path = self.root / worker["output"] / "doc-0001.png"
        with Image.open(image_path) as opened:
            image = opened.copy()
        image.save(image_path, compress_level=0)
        image.close()
        self.change_evidence(worker, lambda item: item.update(sha256=sha256(image_path)))
        self.rejected("Output determinism mismatch")


if __name__ == "__main__":
    unittest.main()
