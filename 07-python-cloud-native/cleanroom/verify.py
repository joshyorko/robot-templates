"""Check saved machine evidence; a recorder hash chain is not authentication.

The run directory and detached carrier are trusted local deployment inputs. This
checks their integrity and RCC's recorded strict decisions, not authenticity of
arbitrary logs supplied by an adversary. Missing evidence never means success.
"""

import argparse
import base64
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import subprocess
import tempfile
import zipfile

from cleanroom.recorder import verify_events
from redactor.integrity import source_digest


HEX = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
RCC_SHA256 = "f8e0394be7f47ee84d6f4cd734822b87b492a4d6102f977f6a18f29453703d48"
RCC_PREVIOUS_SHA256 = "2036b84ec8637e76ea41ab90abd2963357b1fed5ceb9a4e58fe852c67baaff1e"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inside(root, name):
    require(isinstance(name, str) and name and not Path(name).is_absolute(),
            "Evidence paths must be relative")
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()), "Evidence path escapes run root")
    require(path.exists(), f"Missing evidence: {name}")
    return path


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Invalid JSON evidence: {path.name}") from exc


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_decision(record, digest, label):
    require(isinstance(record, dict), f"{label}: invalid RCC evidence")
    require(record.get("artifactDigest") == digest, f"{label}: environment digest mismatch")
    decision = record.get("verification", {})
    require(decision.get("valid") is True and decision.get("code") == "valid"
            and decision.get("policyMode") == "strict-remote"
            and decision.get("artifactDigest") == digest,
            f"{label}: strict signed verification is absent or rejected")
    _fresh(decision.get("verifiedAt"), label + " verification")


def _verify_archive(root, archive, environment_digest=None):
    path = inside(root, archive["path"])
    require(0 < path.stat().st_size <= 256 * 1024 * 1024, "Archive exceeds 256 MiB or is empty")
    require(path.stat().st_size == archive["bytes"], "Archive size mismatch")
    require(sha256(path) == archive["sha256"], "Archive hash mismatch")
    try:
        with zipfile.ZipFile(path) as stream:
            names = stream.namelist()
            require(0 < len(names) <= 32768 and len(names) == archive["members"],
                    "Archive member count mismatch or limit exceeded")
            require(len(set(names)) == len(names), "Archive has duplicate members")
            require(sum(member.file_size for member in stream.infolist()) <= 256 * 1024 * 1024,
                    "Archive uncompressed content exceeds 256 MiB")
            require(all(not Path(name).is_absolute() and ".." not in Path(name).parts
                        for name in names), "Archive has unsafe member paths")
            require(stream.testzip() is None, "Archive member checksum mismatch")
            if environment_digest is not None:
                content = stream.read("rcc-environment/manifest.json")
                manifest = json.loads(content)
                require(manifest.get("artifactDigest") == environment_digest,
                        "Archive environment digest mismatch")
                require(manifest.get("platform", {}).get("rccPlatform") == "linux_amd64",
                        "Archive must target linux_amd64")
                # RCC's canonical identity is this struct serialization with the
                # artifactDigest field omitted. Preserve Go's byte order/escaping.
                identity = content.replace(b',"artifactDigest":"' + environment_digest.encode() + b'"', b"", 1)
                require(identity != content and "sha256:" + hashlib.sha256(identity).hexdigest() == environment_digest,
                        "Archive manifest identity hash mismatch")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("Invalid RCC ZIP archive") from exc


def _verify_carrier(root, carrier):
    try:
        timestamp = datetime.fromisoformat(carrier["sealed_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid carrier seal timestamp") from exc
    require(timestamp.utcoffset() == timedelta(0), "Carrier seal must use UTC")
    age = datetime.now(timezone.utc) - timestamp
    require(timedelta(0) <= age <= timedelta(hours=24), "Carrier seal is future-dated or older than 24 hours")
    files = carrier.get("files")
    require(isinstance(files, dict) and files, "Detached trusted carrier is missing")
    for name, expected in files.items():
        require(isinstance(expected, str) and HEX.fullmatch(expected), "Invalid carrier file digest")
        require(sha256(inside(root, name)) == expected, f"Detached carrier modified: {name}")


def _fresh(timestamp, label):
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label} timestamp") from exc
    require(parsed.utcoffset() == timedelta(0), f"{label} must use UTC")
    age = datetime.now(timezone.utc) - parsed
    require(timedelta(0) <= age <= timedelta(hours=24), f"{label} is stale or future-dated")


def _verify_carrier_binding(root, carrier, digest):
    prefix = "carrier/sha256/" + digest.split(":", 1)[1] + "/"
    expected = {prefix + name + ".json" for name in ("provenance", "sbom", "signature", "revocations")}
    actual = {path.relative_to(root).as_posix() for path in (root / "carrier").rglob("*") if path.is_file()}
    require(set(carrier["files"]) == actual == expected, "Detached carrier membership mismatch")
    attachments = {}
    for name in ("provenance", "sbom", "signature", "revocations"):
        attachment = read_json(inside(root, prefix + name + ".json"))
        require(attachment.get("artifactDigest") == digest, "Detached carrier artifact binding mismatch")
        attachments[name] = attachment
    require(attachments["provenance"].get("platform") == "linux_amd64", "Carrier provenance platform mismatch")
    revocations = attachments["revocations"]
    _fresh(revocations.get("fetchedAt"), "Revocation snapshot")
    require(isinstance(revocations.get("revocations"), list), "Missing revocation snapshot entries")
    roots = read_json(inside(root, "roots.json"))
    valid_keys = set()
    for signature in attachments["signature"].get("signatures", []):
        require(signature.get("artifactDigest") == digest and signature.get("algorithm") == "Ed25519",
                "Invalid detached artifact signature binding or algorithm")
        key_id = signature.get("keyID")
        if key_id not in roots:
            continue
        now = datetime.now(timezone.utc)
        try:
            if signature.get("notBefore"):
                require(datetime.fromisoformat(signature["notBefore"].replace("Z", "+00:00")) <= now,
                        "Artifact signature is not active yet")
            if signature.get("notAfter"):
                require(datetime.fromisoformat(signature["notAfter"].replace("Z", "+00:00")) > now,
                        "Artifact signature expired")
        except (ValueError, TypeError) as exc:
            raise ValueError("Artifact signature has invalid validity window") from exc
        try:
            public = base64.b64decode(roots[key_id] + "=" * (-len(roots[key_id]) % 4), validate=True)
            raw = base64.b64decode(signature["signature"] + "=" * (-len(signature["signature"]) % 4), validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid detached Ed25519 encoding") from exc
        require(len(public) == 32 and len(raw) == 64, "Invalid detached Ed25519 key or signature length")
        message = "\x00".join((digest, key_id, "Ed25519", signature.get("notBefore", ""), signature.get("notAfter", ""))).encode()
        with tempfile.TemporaryDirectory(prefix="cleanroom-verify-") as folder:
            folder = Path(folder)
            (folder / "public.der").write_bytes(bytes.fromhex("302a300506032b6570032100") + public)
            (folder / "signature").write_bytes(raw)
            (folder / "message").write_bytes(message)
            try:
                result = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-keyform", "DER",
                                         "-inkey", str(folder / "public.der"), "-rawin", "-in", str(folder / "message"),
                                         "-sigfile", str(folder / "signature")], capture_output=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError("Independent signature verification blocked: OpenSSL unavailable") from exc
            if result.returncode == 0:
                valid_keys.add(key_id)
    require(valid_keys, "No independently verified trusted artifact signature")
    for revocation in revocations["revocations"]:
        _fresh(revocation.get("updatedAt"), "Revocation entry")
        require(digest not in revocation.get("artifactDigests", []), "Artifact is revoked")
        valid_keys -= set(revocation.get("keyIDs", []))
    require(valid_keys, "All trusted signing keys are revoked")


def _verify_container(root, worker, network_name, homes, run_id, image_id):
    inspected = read_json(inside(root, worker["inspect"]))
    require(isinstance(inspected, list) and len(inspected) == 1, "Expected one inspected worker")
    container = inspected[0]
    require(container.get("Config", {}).get("Labels", {}).get("cleanroom.run") == run_id,
            "Worker container does not belong to this run")
    require(container.get("Config", {}).get("Image") == image_id, "Worker image identity mismatch")
    host = container.get("HostConfig", {})
    require(host.get("NetworkMode") == ("none" if worker["offline"] else network_name),
            f"{worker['name']}: wrong container network")
    require(host.get("Privileged") is False, f"{worker['name']}: privileged worker")
    require(host.get("ReadonlyRootfs") is True, "Worker root filesystem is writable")
    require("ALL" in (host.get("CapDrop") or []), "Worker capabilities were not dropped")
    require(any(value in ("no-new-privileges", "no-new-privileges:true")
                for value in (host.get("SecurityOpt") or [])), "Worker permits new privileges")
    require(not host.get("PortBindings"), "Worker has published ports")
    require(not host.get("CapAdd"), f"{worker['name']}: added container capabilities")
    require(host.get("PidMode", "") != "host" and host.get("IpcMode", "") != "host",
            f"{worker['name']}: host namespace shared")
    mounts = container.get("Mounts", [])
    for mount in mounts:
        require("docker.sock" not in mount.get("Source", "")
                and "podman.sock" not in mount.get("Source", "")
                and "docker.sock" not in mount.get("Destination", "")
                and "podman.sock" not in mount.get("Destination", ""), "Worker has engine socket")
    read_only = {"/src", "/carrier", "/roots.json", "/corpus", "/ca.pem", "/usr/local/bin/rcc",
                 "/environment.rcca" if worker["offline"] else "/token"}
    read_write = {"/work", "/work/output", "/rcc-home"}
    require({mount.get("Destination") for mount in mounts} == read_only | read_write,
            "Worker mount membership mismatch")
    require(all(mount.get("RW") is (mount.get("Destination") in read_write) for mount in mounts),
            "Worker mount write permissions mismatch")
    work = Path(worker["receipt"]).parent
    expected_sources = {"/src": root / "source", "/carrier": root / "carrier", "/roots.json": root / "roots.json",
                        "/corpus": root / "corpus", "/ca.pem": root / "ca.pem", "/work": root / work,
                        "/work/output": root / worker["output"], "/rcc-home": root / work / "home",
                        "/environment.rcca": root / "environment.rcca", "/token": root / "secrets/token"}
    for mount in mounts:
        destination = mount["Destination"]
        if destination in expected_sources:
            require(Path(mount["Source"]).resolve() == expected_sources[destination].resolve(),
                    f"Worker mount source mismatch: {destination}")
    source_mount = [mount for mount in mounts if mount.get("Destination") == "/src"]
    trust_mount = [mount for mount in mounts if mount.get("Destination") == "/carrier"]
    require(len(source_mount) == 1 and source_mount[0].get("RW") is False,
            "Worker source must be mounted read-only at /src")
    require(len(trust_mount) == 1 and trust_mount[0].get("RW") is False,
            "Worker trust must be mounted read-only at /carrier")
    home_mount = [mount for mount in mounts if mount.get("Destination") == "/rcc-home"]
    require(len(home_mount) == 1 and home_mount[0].get("RW") is True,
            "Worker requires its private /rcc-home")
    home = home_mount[0].get("Source")
    require(isinstance(home, str) and home and home not in homes, "Worker RCC homes are shared")
    homes.add(home)
    environment = container.get("Config", {}).get("Env", [])
    require("ROBOCORP_HOME=/rcc-home" in environment, "Worker ROBOCORP_HOME mismatch")
    require("CLEAN_ROOM_SOURCE_DIGEST=" + worker["_source_digest"] in environment,
            "Worker verified source identity is absent")
    probe = read_json(inside(root, worker["network_probe"]))
    require(probe.get("external_reachable") is False and probe.get("provider_loopback_reachable") is False,
            "Worker network probe lacks isolation proof")


def _verify_infrastructure(root, manifest, network_name):
    image_id = manifest["image_id"]
    require(isinstance(image_id, str) and DIGEST.fullmatch(image_id), "Missing immutable container image identity")
    image = read_json(inside(root, "evidence/base-image.json"))
    require(isinstance(image, list) and len(image) == 1 and image[0].get("Id") == image_id
            and image[0].get("Architecture") == "amd64" and image[0].get("Os") == "linux",
            "Base image identity or platform mismatch")
    glibc = read_json(inside(root, "evidence/base-glibc.command.json"))
    require(glibc.get("exit_code") == 0 and glibc.get("timed_out") is False, "glibc worker runtime check failed")
    edge = read_json(inside(root, "evidence/edge-inspect.json"))
    require(isinstance(edge, list) and len(edge) == 1, "Missing isolated edge inspection")
    host = edge[0].get("HostConfig", {})
    require(host.get("NetworkMode") == network_name and host.get("Privileged") is False
            and not host.get("PortBindings"), "Edge network isolation or published-port boundary failed")
    require(edge[0].get("Config", {}).get("Labels", {}).get("cleanroom.run") == manifest["run_id"],
            "Edge belongs to a different run")
    require(edge[0].get("Config", {}).get("Image") == image_id, "Edge image identity mismatch")
    command = edge[0].get("Config", {}).get("Cmd", [])
    require(any("127.0.0.1:8123" in part for part in command), "Provider listener is not loopback-bound")
    boundary = read_json(inside(root, "evidence/edge-boundary.json"))
    require(boundary.get("https") is True and boundary.get("unauthenticated") == 401
            and all(boundary.get(key) == 403 for key in ("admin", "trust_put", "object_put")),
            "Authenticated HTTPS edge boundary probes failed")
    content_length = read_json(inside(root, "evidence/put-content-length.json"))
    require(content_length.get("put_status") in (200, 201, 204) and content_length.get("content_length", 0) > 0,
            "Provider PUT Content-Length acceptance not proven")


def _verify_pixels(pdf_path, png_path, truth, masks):
    # No redactor matching or masking functions are reused here. Truth-labelled
    # glyph positions, not worker agreement, determine what must be obscured.
    try:
        import pypdfium2 as pdfium
        from PIL import Image, ImageChops, ImageDraw
    except ImportError as exc:
        raise ValueError("Pixel verification blocked: pinned PDFium and Pillow are required") from exc
    with closing(pdfium.PdfDocument(pdf_path)) as pdf:
        require(len(pdf) == 1, "Corpus PDF must have one page")
        page = pdf[0]
        textpage = page.get_textpage()
        try:
            original_text = textpage.get_text_range(count=textpage.count_chars())
            bitmap = page.render(scale=2)
            try:
                original = bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
            try:
                expected_masks = []
                for label in truth:
                    require(original_text.count(label["value"]) == 1, "Truth label must occur once in PDF")
                    start = original_text.index(label["value"])
                    boxes = [textpage.get_charbox(index) for index in range(start, start + len(label["value"]))]
                    rectangle = [max(0, math.floor(min(box[0] for box in boxes) * 2) - 2),
                                 max(0, math.floor((page.get_height() - max(box[3] for box in boxes)) * 2) - 2),
                                 min(original.width - 1, math.ceil(max(box[2] for box in boxes) * 2) + 2),
                                 min(original.height - 1, math.ceil((page.get_height() - min(box[1] for box in boxes)) * 2) + 2)]
                    expected_masks.append({"kind": label["kind"], "rectangle": rectangle})
                require(masks == expected_masks, "Redaction masks do not match independent truth geometry")
                with Image.open(png_path) as image:
                    require(image.format == "PNG" and image.mode == "RGB" and image.size == original.size,
                            "Redacted image format or geometry mismatch")
                    require(not image.info, "Redacted PNG contains unexpected metadata")
                    expected = original.copy()
                    try:
                        draw = ImageDraw.Draw(expected)
                        for mask in expected_masks:
                            left, top, right, bottom = mask["rectangle"]
                            with image.crop((left, top, right + 1, bottom + 1)) as sensitive:
                                require(sensitive.getextrema() == ((0, 0), (0, 0), (0, 0)),
                                        "Sensitive pixels remain visible")
                            draw.rectangle(mask["rectangle"], fill="black")
                        difference = ImageChops.difference(expected, image)
                        try:
                            require(difference.getbbox() is None, "Safe pixels modified or sensitive pixels visible")
                        finally:
                            difference.close()
                    finally:
                        expected.close()
            finally:
                original.close()
            return original_text
        finally:
            textpage.close()
            page.close()


def _verify_workload(root, manifest):
    corpus_path = inside(root, manifest["corpus"])
    corpus = read_json(corpus_path / "corpus.json")
    require(corpus.get("schema") == 1 and corpus.get("synthetic") is True
            and corpus.get("mode") == "digital-pdf-not-ocr", "Invalid synthetic corpus")
    documents = corpus.get("documents", [])
    require(documents and len({doc["id"] for doc in documents}) == len(documents), "Empty or duplicate corpus")
    expected_responses = {(round_number, doc["id"]) for round_number in (0, 1) for doc in documents}
    baseline = {}
    for worker in manifest["workers"]:
        output = inside(root, worker["output"])
        results = read_json(inside(root, worker["results"]))
        require(isinstance(results, list) and len(results) == len(expected_responses), "Missing MCP rounds or documents")
        actual = [(result["round"], result["document"]) for result in results]
        require(set(actual) == expected_responses and len(set(actual)) == len(actual), "Duplicate or missing MCP work")
        for result in results:
            doc = next(doc for doc in documents if doc["id"] == result["document"])
            response = result["response"]
            require(isinstance(response, dict) and response.get("isError") is False,
                    "MCP tool returned an error")
            content = response.get("content", [])
            require(len(content) == 1 and content[0].get("type") == "text", "Invalid MCP result content")
            evidence = json.loads(content[0]["text"])
            require(evidence.get("id") == doc["id"] and evidence.get("mode") == "digital-pdf-not-ocr",
                    "Invalid redaction result identity or mode")
            png = inside(output, evidence["redacted_path"])
            text = inside(output, evidence["text_path"])
            require(sha256(png) == evidence["sha256"] and sha256(text) == evidence["text_sha256"],
                    "Redaction output hash mismatch")
            require(read_json(inside(output, doc["id"] + ".json")) == evidence, "Saved and MCP redaction evidence differ")
            actual_text = text.read_text(encoding="utf-8")
            require(actual_text == evidence.get("text") == doc["expected_text"], "Truth redacted text mismatch")
            require(Counter(label["kind"] for label in doc["truth"]) == Counter(mask["kind"] for mask in evidence["redactions"]),
                    "Redaction coverage mismatch")
            for label in doc["truth"]:
                require(label["value"] not in actual_text, "Synthetic sensitive text leaked")
            for safe in doc["safe_text"]:
                require(safe in actual_text, "Safe text lost")
            pdf = inside(corpus_path, doc["pdf"])
            require(sha256(pdf) == doc["sha256"], "Input PDF hash mismatch")
            original = _verify_pixels(pdf, png, doc["truth"], evidence["redactions"])
            expected = original
            for label in doc["truth"]:
                expected = expected.replace(label["value"], "[REDACTED:" + label["kind"] + "]")
            require("\n".join(expected.splitlines()) + "\n" == actual_text, "Independent text redaction mismatch")
            if doc["id"] in baseline:
                require(baseline[doc["id"]] == evidence, "Output determinism mismatch across rounds or workers")
            else:
                baseline[doc["id"]] = evidence
    return len(documents)


def verify(root, *, require_closure=True):
    root = Path(root).resolve()
    try:
        manifest = read_json(inside(root, "run.json"))
        require(not any(path.is_file() for path in (root / "secrets").rglob("*")),
                "Ephemeral demo secrets have not been removed")
        require(manifest.get("schema") == 1, "Unsupported run manifest")
        require(isinstance(manifest.get("run_id"), str) and manifest["run_id"], "Missing run identity")
        require(DIGEST.fullmatch(manifest["environment_digest"]), "Invalid environment identity")
        require(DIGEST.fullmatch(manifest["source_digest"]), "Invalid source identity")
        require(HEX.fullmatch(manifest["rcc_sha256"]), "Missing RCC binary identity")
        require(manifest["rcc_sha256"] in (RCC_SHA256, RCC_PREVIOUS_SHA256)
                and sha256(inside(root, manifest["rcc_binary"])) == manifest["rcc_sha256"],
                "RCC binary checksum mismatch")
        source = inside(root, manifest["source"])
        require({path.name for path in source.iterdir()} == {
            "redactor", "cleanroom", "clean-room-robot.yaml", "clean-room-environment.yaml"},
            "Staged source top-level membership mismatch")
        source_files = list((source / "redactor").rglob("*.py"))
        require(source_files and all(path.resolve().is_relative_to(source.resolve())
                                     for path in source_files), "Source package is empty or escapes source root")
        require(source_digest(source) == manifest["source_digest"],
                "Source digest mismatch")
        require(sha256(inside(root, manifest["corpus"]) / "corpus.json") == manifest["corpus_sha256"],
                "Corpus truth manifest hash mismatch")
        require(sha256(inside(root, "roots.json")) == manifest["roots_sha256"], "Site trust roots hash mismatch")
        _verify_archive(root, manifest["archive"], manifest["environment_digest"])
        _verify_carrier(root, manifest["carrier"])
        _verify_carrier_binding(root, manifest["carrier"], manifest["environment_digest"])
        workers = manifest["workers"]
        require(isinstance(workers, list) and 3 <= len(workers) <= 5, "Expected two to four online workers and one offline worker")
        require(all(isinstance(worker.get("offline"), bool) for worker in workers), "Worker mode must be explicit")
        require(sum(worker["offline"] for worker in workers) == 1, "Expected exactly one offline worker")
        require(len({worker["name"] for worker in workers}) == len(workers), "Duplicate worker name")
        network = read_json(inside(root, manifest["network"]))
        require(isinstance(network, list) and len(network) == 1 and network[0].get("Internal") is True,
                "Worker network is not isolated/internal")
        network_name = network[0].get("Name")
        require(isinstance(network_name, str) and network_name not in ("host", "bridge", "none", ""), "Invalid isolated network name")
        require(network[0].get("Labels", {}).get("cleanroom.run") == manifest["run_id"],
                "Network belongs to a different run")
        _verify_infrastructure(root, manifest, network_name)
        homes = set()
        leases = set()
        for worker in workers:
            acquisition = read_json(inside(root, worker["acquire"]))
            strict_decision(acquisition, manifest["environment_digest"], worker["name"] + " acquire")
            receipt = read_json(inside(root, worker["receipt"]))
            strict_decision(receipt, manifest["environment_digest"], worker["name"] + " exec")
            require(receipt.get("status") == "completed" and receipt.get("exitCode") == 0
                    and isinstance(receipt.get("leaseId"), str) and receipt["leaseId"],
                    f"{worker['name']}: execution has no successful closure")
            require(receipt["leaseId"] not in leases, "Workers reused execution lease")
            leases.add(receipt["leaseId"])
            _verify_container(root, dict(worker, _source_digest=manifest["source_digest"]), network_name,
                              homes, manifest["run_id"], manifest["image_id"])
        events = verify_events(root)
        require(events, "Flight recorder is empty")
        if require_closure:
            closure = read_json(inside(root, "closure.json"))
            require(closure.get("status") == "completed", "Run is active, blocked, or failed")
            require(events[-1].get("kind") == "closure" and events[-1].get("evidence") == "closure.json",
                    "Flight recorder has no final closure evidence")
        count = _verify_workload(root, manifest)
        return {"status": "verified", "run_id": manifest["run_id"], "documents": count,
                "workers": len(workers), "mcp_rounds_per_worker": 2,
                "environment_digest": manifest["environment_digest"],
                "source_digest": manifest["source_digest"],
                "trust_scope": "artifact Ed25519 independently checked against site roots; local run evidence and detached metadata are trusted deployment inputs"}
    except (KeyError, TypeError, AttributeError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Incomplete or invalid run evidence: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root)
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
