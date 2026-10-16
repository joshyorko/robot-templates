"""Bounded native CLI probes; metadata fixtures are never environment proof."""

import argparse
import base64
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cleanroom.control import Lab, check_binary, write_json
from cleanroom.recorder import report


def canonical(path, value):
    Path(path).write_text(json.dumps(value, separators=(",", ":")))


def run(root):
    lab = Lab(root)
    lab.rcc = check_binary(shutil.which("rcc"))
    lab.recorder.event("run", status="active", scope="native bounded spikes, no container acceptance")
    keys = lab.root / "ephemeral"
    keys.mkdir(mode=0o700)
    results = {}
    try:
        lab.command([lab.rcc, "version"], "rcc-version",
                    env=dict(os.environ, ROBOCORP_HOME=str(lab.root / "preflight-home"), RCC_HOLOTREE_MODE="private"))
        lab.command(["openssl", "genpkey", "-algorithm", "ED25519", "-out", keys / "signing.pem"], "keygen")
        lab.command(["openssl", "pkey", "-in", keys / "signing.pem", "-pubout", "-outform", "DER", "-out", keys / "public.der"], "public-key")
        lab.command(["openssl", "pkey", "-in", keys / "signing.pem", "-outform", "DER", "-out", keys / "private.der"], "private-seed")
        private = (keys / "private.der").read_bytes()
        if private[:-32].hex() != "302e020100300506032b657004220420":
            raise RuntimeError("Unexpected Ed25519 encoding")
        (keys / "seed").write_bytes(private[-32:])
        artifact = "sha256:" + hashlib.sha256(b"metadata-contract-fixture-not-an-environment").hexdigest()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = now.isoformat().replace("+00:00", "Z")
        (keys / "message").write_bytes((artifact + "\0demo-spike\0Ed25519\0\0").encode())
        lab.command(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", keys / "signing.pem",
                     "-in", keys / "message", "-out", keys / "signature"], "sign-fixture")
        canonical(lab.root / "roots.json", {"demo-spike": base64.b64encode((keys / "public.der").read_bytes()[-32:]).decode().rstrip("=")})
        media = "application/vnd.rcc.environment."
        canonical(lab.root / "provenance.json", {"mediaType": media + "provenance.v1+json", "artifactDigest": artifact,
                  "specificationDigest": artifact, "platform": "linux_amd64", "builder": "demo-spike",
                  "rccVersion": "v18.19.7", "createdAt": stamp})
        canonical(lab.root / "sbom.json", {"mediaType": media + "sbom.spdx+json", "artifactDigest": artifact, "components": []})
        signature = {"mediaType": media + "signature.v1+json", "artifactDigest": artifact,
                     "keyID": "demo-spike", "algorithm": "Ed25519",
                     "signature": base64.b64encode((keys / "signature").read_bytes()).decode().rstrip("=")}
        canonical(lab.root / "signatures.json", [signature])
        revocations = {"mediaType": media + "revocations.v1+json", "artifactDigest": artifact,
                       "revocations": [], "fetchedAt": stamp, "source": "local-demo-fixture"}
        canonical(lab.root / "revocations.json", revocations)
        command = [lab.rcc, "env", "trust", "verify", "--artifact", artifact, "--platform", "linux_amd64",
                   "--builder", "demo-spike", "--provenance", lab.root / "provenance.json", "--sbom", lab.root / "sbom.json",
                   "--signatures", lab.root / "signatures.json", "--trust-roots", lab.root / "roots.json",
                   "--revocations", lab.root / "revocations.json", "--strict-remote", "--json"]
        code, raw = lab.command(command, "strict-metadata", check=False)
        results["strict_metadata"] = {"exit_code": code, "receipt": json.loads(raw) if raw.strip() else None,
                                      "scope": "signed metadata fixture, NOT acquisition or execution"}
        canonical(lab.root / "signatures.json", [])
        rejected, _ = lab.command(command, "unsigned-metadata", check=False)
        results["unsigned_rejected"] = rejected != 0
        canonical(lab.root / "signatures.json", [signature])
        revocations["fetchedAt"] = (now - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        canonical(lab.root / "revocations.json", revocations)
        stale, _ = lab.command(command, "stale-metadata", check=False)
        results["stale_rejected"] = stale != 0
        for engine in ("docker", "podman"):
            engine_code, _ = lab.command([engine, "info"], engine + "-info", check=False, timeout=15)
            results[engine] = {"exit_code": engine_code}
        provider_code, _ = lab.command([lab.rcc, "cache", "serve", "--root", lab.root / "provider", "--listen", "127.0.0.1:0", "--json"],
                                      "loopback-provider", check=False, timeout=3,
                                      env=dict(os.environ, ROBOCORP_HOME=str(lab.root / "provider-home"), RCC_HOLOTREE_MODE="private"))
        results["loopback_provider"] = {"exit_code": provider_code}
        publish_code, _ = lab.command([lab.rcc, "env", "publish", "--robot", "clean-room-robot.yaml", "--provider", "local",
                                      "--trust-carrier", lab.root / "carrier", "--signing-key", keys / "seed", "--signing-key-id", "demo-spike", "--json"],
                                     "signed-publish", check=False, timeout=30,
                                     env=dict(os.environ, ROBOCORP_HOME=str(lab.root / "builder-home"), RCC_HOLOTREE_MODE="private"))
        results["signed_publish"] = {"exit_code": publish_code}
        results["not_exercised"] = ["separate-home strict acquire", "same-digest rotation", "archive export/size/offline import",
                                    "provider HTTPS container routing", "RCC leased MCP workload", "PDF pixels"]
        write_json(lab.root / "spikes.json", results)
    finally:
        shutil.rmtree(keys)
        write_json(lab.root / "closure.json", {"status": "blocked", "reason": "Bounded CLI probes only; container e2e not proven"})
        lab.recorder.event("closure", status="blocked", evidence="closure.json")
        report(lab.root)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(run(args.root), indent=2))
