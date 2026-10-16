"""Dakota-local conductor. Only the builder has package-download access.

No worker receives an engine socket. All exec/stdio mediation happens here.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cleanroom.recorder import Recorder, report, start_server
from redactor.corpus import generate
from redactor.integrity import source_digest

RCC_SHA256 = "f8e0394be7f47ee84d6f4cd734822b87b492a4d6102f977f6a18f29453703d48"
RCC_PREVIOUS_SHA256 = "2036b84ec8637e76ea41ab90abd2963357b1fed5ceb9a4e58fe852c67baaff1e"
TEMPLATE = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = "docker.io/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value, *, canonical=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=None if canonical else 2, sort_keys=True,
                                    separators=(",", ":") if canonical else None) + "\n")
    temporary.replace(path)


def check_binary(path):
    if sha(path) not in (RCC_SHA256, RCC_PREVIOUS_SHA256):
        raise ValueError("RCC binary checksum mismatch; refusing execution")
    return str(Path(path).resolve())


def resource_names(run_id):
    if not re.fullmatch(r"cr-[0-9a-f]{12}", run_id):
        raise ValueError("invalid owned resource identifier")
    return {"network": run_id + "-net", "builder": run_id + "-builder",
            "edge": run_id + "-edge"}


def local_endpoint(endpoint):
    return (isinstance(endpoint, str) and endpoint.startswith("unix:///")
            and "?" not in endpoint and "#" not in endpoint
            and len(endpoint) > len("unix:///"))


def archive_info(path):
    path = Path(path)
    if path.stat().st_size > 256 << 20:
        raise ValueError("archive exceeds 256 MiB")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 32768 or sum(x.file_size for x in members) > 256 << 20:
            raise ValueError("archive exceeds member/uncompressed budget")
        names = [x.filename for x in members]
        for name in names:
            if (name.startswith("/") or "\\" in name or
                    ".." in PurePosixPath(name).parts):
                raise ValueError("unsafe archive member")
        if len(set(names)) != len(names):
            raise ValueError("duplicate archive member")
        manifest = json.loads(archive.read("rcc-environment/manifest.json"))
    return {"path": path.name, "sha256": sha(path), "bytes": path.stat().st_size,
            "members": len(members), "manifest": manifest}


class Lab:
    def __init__(self, root, engine="docker", rcc=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.recorder = Recorder(self.root)
        self.engine = engine
        self.rcc = rcc or shutil.which("rcc")
        self.run_id = "cr-" + secrets.token_hex(6)
        self.names = resource_names(self.run_id)
        self.resources = []
        self.counter = 0
        self.image = None
        self.server = None

    def command(self, args, name=None, check=True, timeout=300, env=None):
        self.counter += 1
        name = name or f"command-{self.counter:03}"
        log = self.root / "evidence" / name
        log.parent.mkdir(exist_ok=True)
        self.recorder.event("command-start", command=name, argv=[str(x) for x in args],
                            status="active")
        started = time.monotonic()
        timed_out = False
        try:
            with log.with_suffix(".stdout").open("wb") as stdout, log.with_suffix(".stderr").open("wb") as stderr:
                process = subprocess.Popen([str(x) for x in args], stdout=stdout, stderr=stderr,
                                           env=env, start_new_session=True)
                try:
                    code = process.wait(timeout=timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise
        except subprocess.TimeoutExpired:
            code, timed_out = 124, True
        except OSError as exc:
            log.with_suffix(".stderr").write_text(f"{type(exc).__name__}: {exc}\n")
            code = 127
        observation = {"argv": [str(x) for x in args], "exit_code": code,
                       "seconds": time.monotonic() - started, "timed_out": timed_out,
                       "stdout": str(log.with_suffix(".stdout").relative_to(self.root)),
                       "stderr": str(log.with_suffix(".stderr").relative_to(self.root))}
        write_json(log.with_suffix(".command.json"), observation)
        self.recorder.event("command-exit", command=name, **observation)
        if check and code:
            raise RuntimeError(f"{name} exited {code}; inspect {log.with_suffix('.stderr')}")
        return code, log.with_suffix(".stdout").read_text(errors="replace")

    def engine_json(self, args, name):
        _, output = self.command([self.engine, *args], name)
        value = json.loads(output)
        write_json(self.root / "evidence" / f"{name}.json", value)
        return value

    def preflight(self):
        if sys.platform != "linux" or os.uname().machine != "x86_64":
            raise RuntimeError("Clean Room requires linux_amd64")
        if not self.rcc:
            raise RuntimeError("Install checksum-verified RCC v18.19.7 first")
        self.rcc = check_binary(self.rcc)
        self.command([self.rcc, "version"], "rcc-version",
                     env=dict(os.environ, ROBOCORP_HOME=str(self.root / "preflight-home"), RCC_HOLOTREE_MODE="private"))
        if self.engine == "docker":
            # Observe the selected context; never switch it or use a remote daemon.
            context = self.engine_json(["context", "inspect"], "engine-context")
            endpoint = os.environ.get("DOCKER_HOST") or context[0]["Endpoints"]["docker"]["Host"]
            if not local_endpoint(endpoint):
                raise RuntimeError("Dakota-only: remote container endpoint rejected")
        elif any(os.environ.get(name) for name in ("CONTAINER_HOST", "CONTAINER_CONNECTION")):
            raise RuntimeError("Dakota-only: remote Podman settings rejected")
        # Inspect both independently before choosing a backend. Never change contexts.
        code, _ = self.command([self.engine, "info"], "engine-info", check=False, timeout=30)
        if code:
            raise RuntimeError("Local container engine inaccessible; no isolation bypass")
        image = os.environ.get("CLEAN_ROOM_IMAGE", DEFAULT_IMAGE)
        info = self.engine_json(["image", "inspect", image], "base-image")[0]
        if info.get("Architecture") != "amd64" or info.get("Os") != "linux":
            raise RuntimeError("Base image must be linux/amd64 glibc Python 3.12")
        # An already-installed prerequisite is selected once, then pinned by ID.
        self.image = info["Id"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image):
            raise RuntimeError("Container engine did not supply immutable image ID")
        self.command([self.engine, "run", "--rm", "--pull=never", "--network=none",
                      "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                      self.image, "python", "-c",
                      "import platform,sys; assert platform.libc_ver()[0]=='glibc'; assert sys.version_info[:2]==(3,12)"],
                     "base-glibc", timeout=30)

    def mount(self, source, target, readonly=True):
        source = Path(source).resolve()
        if "," in str(source):
            raise ValueError("commas unsupported in bind mount paths")
        return ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]

    def container(self, name, network, mounts, command=None, extra=None):
        args = [self.engine, "run", "-d", "--pull=never", "--name", name,
                "--label", f"cleanroom.run={self.run_id}", "--platform", "linux/amd64",
                "--network", network, "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--pids-limit=256", "--memory=2g",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                "-e", "HOME=/tmp", "-e", "USER=clean-room", "-e", "LOGNAME=clean-room",
                "-e", "ROBOCORP_HOME=/rcc-home",
                "-e", "RCC_HOLOTREE_MODE=private", "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-e", "PYTHONPATH=/src", "--workdir", "/src"]
        for source, target, readonly in mounts:
            args += self.mount(source, target, readonly)
        args += extra or []
        args += [self.image, *(command or ["sleep", "infinity"])]
        self.resources.append(name)
        self.save_resources()
        self.command(args, f"start-{name}", timeout=60)

    def save_resources(self):
        write_json(self.root / "resources.json", {"engine": self.engine, "run_id": self.run_id,
                   "containers": self.resources, "network": self.names["network"]})

    def execute(self, name, args, evidence, check=True, timeout=900):
        return self.command([self.engine, "exec", name, *args], evidence, check, timeout)

    def json_exec(self, name, args, evidence, timeout=900):
        _, output = self.execute(name, args, evidence, timeout=timeout)
        value = json.loads(output)
        write_json(self.root / "evidence" / f"{evidence}.json", value)
        return value

    def prepare(self):
        for name in ("source", "corpus", "builder", "provider", "carrier", "secrets", "workers", "tools", "edge-home"):
            (self.root / name).mkdir(mode=0o700)
        shutil.copyfile(self.rcc, self.root / "tools/rcc")
        (self.root / "tools/rcc").chmod(0o700)
        self.rcc = check_binary(self.root / "tools/rcc")
        (self.root / "builder/artifacts/v1").mkdir(parents=True)
        for package in ("redactor", "cleanroom"):
            shutil.copytree(TEMPLATE / package, self.root / "source" / package,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("clean-room-robot.yaml", "clean-room-environment.yaml"):
            shutil.copyfile(TEMPLATE / name, self.root / "source" / name)
        self.source = source_digest(self.root / "source")
        # Hash externally before any worker interpreter sees the immutable mount.
        if self.source != source_digest(TEMPLATE):
            raise RuntimeError("Source changed during staging")
        generate(self.root / "corpus", self.documents)
        self.manifest = {"schema": 1, "run_id": self.run_id, "source": "source",
                         "source_digest": self.source, "rcc_sha256": sha(self.rcc),
                         "rcc_binary": "tools/rcc",
                         "corpus": "corpus", "corpus_sha256": sha(self.root / "corpus/corpus.json"),
                         "network": "evidence/network.json", "workers": [], "image_id": self.image}
        secret = self.root / "secrets"
        (secret / "token").write_text(secrets.token_urlsafe(32))
        self.command(["openssl", "genpkey", "-algorithm", "ED25519", "-out", secret / "signing.pem"], "demo-keygen")
        self.command(["openssl", "pkey", "-in", secret / "signing.pem", "-outform", "DER", "-out", secret / "signing.der"], "demo-seed")
        self.command(["openssl", "pkey", "-in", secret / "signing.pem", "-pubout", "-outform", "DER", "-out", secret / "public.der"], "demo-public")
        private = (secret / "signing.der").read_bytes()
        public = (secret / "public.der").read_bytes()
        if private[:-32].hex() != "302e020100300506032b657004220420" or public[:-32].hex() != "302a300506032b6570032100":
            raise RuntimeError("Unexpected OpenSSL Ed25519 key encoding")
        (secret / "seed").write_bytes(private[-32:])
        write_json(self.root / "roots.json", {"clean-room-demo": base64.b64encode(public[-32:]).decode().rstrip("=")}, canonical=True)
        self.manifest["roots_sha256"] = sha(self.root / "roots.json")
        self.command(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                      "-subj", "/CN=clean-room-edge", "-addext", "subjectAltName=DNS:clean-room-edge",
                      "-keyout", secret / "tls.key", "-out", self.root / "ca.pem"], "demo-tls")

    def build(self):
        name = self.names["builder"]
        self.container(name, "bridge", [(self.rcc, "/usr/local/bin/rcc", True),
                       (self.root / "source", "/src", True), (self.root / "builder", "/rcc-home", False),
                       (self.root / "provider", "/rcc-home/artifacts/v1/provider", False),
                       (self.root / "carrier", "/carrier", False), (self.root / "secrets/seed", "/seed", True),
                       (self.root / "roots.json", "/roots.json", True),
                       (self.root, "/run-data", False)])
        published = self.json_exec(name, ["rcc", "env", "publish", "--robot", "/src/clean-room-robot.yaml",
                      "--provider", "local", "--trust-carrier", "/carrier", "--trust-carrier-type", "filesystem",
                      "--signing-key", "/seed", "--signing-key-id", "clean-room-demo", "--json"], "publish", 1200)
        digest = published["artifactDigest"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise RuntimeError("Publish did not return canonical environment digest")
        self.manifest["environment_digest"] = digest
        self.json_exec(name, ["rcc", "env", "export", "--artifact", digest, "--provider", "local",
                      "--output", "/run-data/environment.rcca"], "export")
        self.manifest["archive"] = archive_info(self.root / "environment.rcca")
        self.manifest["carrier"] = {"sealed_at": datetime.now(timezone.utc).isoformat(), "files": {
            str(p.relative_to(self.root)): sha(p) for p in sorted((self.root / "carrier").rglob("*.json"))}}
        if len(self.manifest["carrier"]["files"]) < 4:
            raise RuntimeError("Detached trust carrier incomplete")
        write_json(self.root / "run.json", self.manifest)
        self.recorder.event("sealed", environment_digest=digest, source_digest=self.source,
                            archive_bytes=self.manifest["archive"]["bytes"], mode="digital-pdf-not-ocr")
        # Repeat publish into a DIFFERENT carrier; never mutate the trusted original.
        # No key rotation unless empirical payload identity and trusted carrier update work.
        (self.root / "repeat-carrier").mkdir()
        repeated = self.json_exec(name, ["rcc", "env", "publish", "--robot", "/src/clean-room-robot.yaml",
                       "--provider", "local", "--trust-carrier", "/run-data/repeat-carrier",
                       "--trust-carrier-type", "filesystem", "--signing-key", "/seed",
                       "--signing-key-id", "clean-room-demo", "--json"], "repeat-publish", 1200)
        write_json(self.root / "rotation-spike.json", {
            "same_digest": repeated.get("artifactDigest") == digest,
            "status": "not-demonstrated", "reason": "Rotation is optional; no trusted carrier update performed",
            "environment_payload_uploaded_bytes": repeated.get("uploadedBytes")})
        self.execute(name, ["rcc", "env", "acquire", "--archive", "/run-data/environment.rcca",
                     *self.trust_args(), "--json"], "builder-strict-acquire")
        self.command([self.engine, "stop", name], "stop-builder")
        for filename in ("seed", "signing.pem", "signing.der", "public.der"):
            (self.root / "secrets" / filename).unlink()

    def trust_args(self):
        return ["--trust-carrier", "/carrier", "--trust-carrier-type", "filesystem",
                "--trust-roots", "/roots.json", "--strict-remote"]

    def edge(self):
        network = self.names["network"]
        self.command([self.engine, "network", "create", "--internal", "--label",
                      f"cleanroom.run={self.run_id}", network], "create-network")
        self.engine_json(["network", "inspect", network], "network")
        # Provider loopback and HTTPS proxy share exactly one container namespace.
        startup = ("import subprocess,sys; "
                   "p=subprocess.Popen(['rcc','cache','serve','--root','/provider','--listen','127.0.0.1:8123','--json'],stdout=open('/tmp/provider.json','w')); "
                   "q=subprocess.Popen([sys.executable,'-m','cleanroom.edge','--cert','/ca.pem','--key','/secrets/tls.key','--token-file','/secrets/token','--publish']); "
                   "sys.exit(q.wait())")
        self.container(self.names["edge"], network,
                       [(self.rcc, "/usr/local/bin/rcc", True), (self.root / "source", "/src", True),
                        (self.root / "edge-home", "/rcc-home", False),
                        (self.root / "provider", "/provider", False), (self.root / "ca.pem", "/ca.pem", True),
                        (self.root / "secrets", "/secrets", True)],
                       ["python", "-c", startup], ["--network-alias", "clean-room-edge"])
        # Read credentials in-container from mounted file, never in argv/env/logs.
        probe = ("import hashlib,http.client,json,ssl,time; "
                 "c=ssl.create_default_context(cafile='/ca.pem'); "
                 "h={'Authorization':'Bearer '+open('/secrets/token').read().strip()}; "
                 "p=b'clean-room Content-Length probe'; "
                 "url='/v1/objects/sha256/'+hashlib.sha256(p).hexdigest(); "
                 "conn=http.client.HTTPSConnection('clean-room-edge',8443,context=c,timeout=10); "
                 "conn.request('PUT',url,body=p,headers=dict(h,**{'Content-Length':str(len(p)),'Content-Type':'application/octet-stream'})); "
                 "r=conn.getresponse(); status=r.status; r.read(); assert status in (200,201,204),status; "
                 "print(json.dumps({'put_status':status,'content_length':len(p)}))")
        # Readiness wait is bounded and observes the same process, no restarts.
        ready = ("import http.client,ssl,time,json\n"
                 "ctx=ssl.create_default_context(cafile='/ca.pem')\n"
                 "headers={'Authorization':'Bearer '+open('/secrets/token').read().strip()}\n"
                 "last='not ready'\n"
                 "for i in range(100):\n"
                 " try:\n"
                 "  c=http.client.HTTPSConnection('clean-room-edge',8443,context=ctx,timeout=1)\n"
                 "  c.request('GET','/v1/health',headers=headers); r=c.getresponse(); body=r.read(); status=r.status; c.close()\n"
                 "  if status==200 and json.loads(body).get('ready') is True:break\n"
                 "  last=str(status)\n"
                 " except OSError as e:last=type(e).__name__\n"
                 " time.sleep(.1)\n"
                 "else:raise RuntimeError('edge readiness timeout '+last)\n"
                 "print(json.dumps({'ready':True,'https':True}))")
        self.execute(self.names["edge"], ["python", "-c", ready], "edge-ready", timeout=20)
        self.json_exec(self.names["edge"], ["python", "-c", probe], "put-content-length")
        # Freeze the public service as read-only after the isolated PUT spike.
        # The proxy is replaced by ending/recreating only this run-owned container.
        self.command([self.engine, "rm", "-f", self.names["edge"]], "edge-publisher-close")
        startup = startup.replace(", '--publish'", "").replace(",'--publish'", "")
        self.container(self.names["edge"], network,
                       [(self.rcc, "/usr/local/bin/rcc", True), (self.root / "source", "/src", True),
                        (self.root / "edge-home", "/rcc-home", False),
                        (self.root / "provider", "/provider", False), (self.root / "ca.pem", "/ca.pem", True),
                        (self.root / "secrets", "/secrets", True)],
                       ["python", "-c", startup], ["--network-alias", "clean-room-edge"])
        self.execute(self.names["edge"], ["python", "-c", ready], "edge-readonly-ready", timeout=20)
        self.engine_json(["inspect", self.names["edge"]], "edge-inspect")
        boundary = ("import http.client,ssl,json\n"
                    "ctx=ssl.create_default_context(cafile='/ca.pem')\n"
                    "auth={'Authorization':'Bearer '+open('/secrets/token').read().strip()}\n"
                    "checks={}\n"
                    "for key,method,path,headers in [('unauthenticated','GET','/v1/health',{}),('admin','GET','/v1/admin/objects',auth),('trust_put','PUT','/sha256/" + self.manifest["environment_digest"][7:] + "/signature.json',auth),('object_put','PUT','/v1/objects/sha256/" + "0" * 64 + "',auth)]:\n"
                    " c=http.client.HTTPSConnection('clean-room-edge',8443,context=ctx,timeout=5)\n"
                    " c.request(method,path,body=b'',headers=headers);r=c.getresponse();checks[key]=r.status;r.read();c.close()\n"
                    "assert checks=={'unauthenticated':401,'admin':403,'trust_put':403,'object_put':403},checks\n"
                    "checks['https']=True;print(json.dumps(checks))")
        self.json_exec(self.names["edge"], ["python", "-c", boundary], "edge-boundary")

    def worker(self, index, offline=False):
        short = "offline" if offline else f"worker-{index}"
        name = self.run_id + "-" + short
        directory = self.root / "workers" / short
        for sub in ("home", "output"):
            (directory / sub).mkdir(parents=True, mode=0o700)
        mounts = [(self.rcc, "/usr/local/bin/rcc", True), (self.root / "source", "/src", True),
                  (directory / "home", "/rcc-home", False), (directory / "output", "/work/output", False),
                  (directory, "/work", False), (self.root / "carrier", "/carrier", True),
                  (self.root / "roots.json", "/roots.json", True), (self.root / "corpus", "/corpus", True),
                  (self.root / "ca.pem", "/ca.pem", True)]
        if offline:
            mounts.append((self.root / "environment.rcca", "/environment.rcca", True))
        else:
            mounts.append((self.root / "secrets/token", "/token", True))
        self.container(name, "none" if offline else self.names["network"], mounts,
                       extra=["-e", "SSL_CERT_FILE=/ca.pem", "-e", "CLEAN_ROOM_SOURCE_DIGEST=" + self.source,
                              "-e", "CLEAN_ROOM_CORPUS=/corpus", "-e", "CLEAN_ROOM_OUTPUT=/work/output"])
        self.engine_json(["inspect", name], f"{short}-inspect")
        probe = ("import socket,json\n"
                 "def reachable(host,port):\n"
                 " try:\n"
                 "  s=socket.create_connection((host,port),1);s.close();return True\n"
                 " except OSError:return False\n"
                 "result={'external_reachable':reachable('1.1.1.1',443),'provider_loopback_reachable':reachable('clean-room-edge',8123)}\n"
                 "assert not any(result.values()),result\nprint(json.dumps(result))")
        self.json_exec(name, ["python", "-c", probe], f"{short}-network-probe")
        if offline:
            acquire = ["rcc", "env", "acquire", "--archive", "/environment.rcca", *self.trust_args(), "--json"]
        else:
            self.execute(name, ["rcc", "provider", "add", "clean-room", "--type", "http", "--url",
                         "https://clean-room-edge:8443/", "--authorization-env", "CLEAN_ROOM_AUTH", "--json"], short + "-provider")
            acquire = ["rcc", "env", "acquire", "--artifact", self.manifest["environment_digest"],
                       "--provider", "clean-room", *self.trust_args(), "--json"]
        # Wrapper reads secret locally; no shell expansion, no credential in a receipt.
        wrapper = "import os,subprocess,sys; os.environ['CLEAN_ROOM_AUTH']='Bearer '+open('/token').read().strip(); sys.exit(subprocess.call(sys.argv[1:]))"
        if not offline:
            acquire = ["python", "-c", wrapper, *acquire]
        acquired = self.json_exec(name, acquire, f"{short}-acquire")
        if acquired.get("artifactDigest") != self.manifest["environment_digest"] or not acquired.get("verification", {}).get("valid"):
            raise RuntimeError("Strict acquisition evidence missing")
        record = {"name": short, "offline": offline, "acquire": f"evidence/{short}-acquire.json",
                  "receipt": f"workers/{short}/receipt.json", "output": f"workers/{short}/output",
                  "results": f"evidence/{short}-results.json", "inspect": f"evidence/{short}-inspect.json"}
        record["network_probe"] = f"evidence/{short}-network-probe.json"
        self.manifest["workers"].append(record)
        write_json(self.root / "run.json", self.manifest)
        self.mcp(name, short, offline, wrapper)

    def mcp(self, name, short, offline, wrapper):
        # Verify outside the source interpreter immediately before starting the lease.
        if source_digest(self.root / "source") != self.source:
            raise RuntimeError("Immutable source mismatch before exec")
        args = ["rcc", "env", "exec", "--artifact", self.manifest["environment_digest"],
                *([] if offline else ["--provider", "clean-room"]),
                *self.trust_args(), "--inherit-streams", "--receipt-file", "/work/receipt.json",
                "--", "python", "-m", "redactor.mcp"]
        if not offline:
            args = ["python", "-c", wrapper, *args]
        stderr_path = self.root / "evidence" / f"{short}-mcp.stderr"
        results = []
        self.recorder.event("lease", worker=short, status="active", receipt_present=False)
        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen([self.engine, "exec", "-i", name, *args], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=stderr, bufsize=0)
            reader = selectors.DefaultSelector()
            reader.register(process.stdout, selectors.EVENT_READ)
            buffer = b""
            identity = 0

            def request(method, params):
                nonlocal identity, buffer
                identity += 1
                process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": identity,
                                                "method": method, "params": params}) + "\n").encode())
                process.stdin.flush()
                deadline = time.monotonic() + 120
                while b"\n" not in buffer:
                    if time.monotonic() >= deadline or not reader.select(max(0, deadline - time.monotonic())):
                        raise RuntimeError("MCP response timeout")
                    data = os.read(process.stdout.fileno(), 65536)
                    if not data:
                        raise RuntimeError("MCP stream closed before response")
                    buffer += data
                    if len(buffer) > 4 << 20:
                        raise RuntimeError("MCP response exceeds limit")
                line, buffer = buffer.split(b"\n", 1)
                response = json.loads(line)
                if response.get("id") != identity or response.get("jsonrpc") != "2.0" or "error" in response:
                    raise RuntimeError("Invalid MCP response")
                return response["result"]

            try:
                initialized = request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                     "clientInfo": {"name": "clean-room-conductor", "version": "1"}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                process.stdin.flush()
                listed = request("tools/list", {})
                if not any(x.get("name") == "redact_document" for x in listed.get("tools", [])):
                    raise RuntimeError("MCP redaction tool missing")
                for round_number in range(2):
                    for document in json.loads((self.root / "corpus/corpus.json").read_text())["documents"]:
                        result = request("tools/call", {"name": "redact_document", "arguments": {"document": document["id"]}})
                        results.append({"round": round_number, "document": document["id"], "response": result})
                        write_json(self.root / f"evidence/{short}-results.json", results)
                        if result.get("isError"):
                            raise RuntimeError("MCP tool reported failure")
                        self.recorder.event("document", worker=short, document=document["id"], round=round_number,
                                            status="active", evidence=f"evidence/{short}-results.json")
                process.stdin.close()
                code = process.wait(timeout=30)
                # Completion exists only after real process closure and atomic RCC receipt.
                receipt_path = self.root / "workers" / short / "receipt.json"
                if code or not receipt_path.is_file():
                    raise RuntimeError("MCP closed without successful RCC receipt")
                receipt = json.loads(receipt_path.read_text())
                if receipt.get("status") != "completed" or receipt.get("exitCode") != 0:
                    raise RuntimeError("RCC lease failed")
                self.recorder.event("lease", worker=short, status="completed", receipt_present=True,
                                    evidence=str(receipt_path.relative_to(self.root)))
            finally:
                reader.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()

    def run(self, workers=2, documents=4, serve=True):
        self.documents = documents
        self.recorder.event("run", status="active", host=os.uname().nodename, run_id=self.run_id)
        failure = None
        try:
            self.preflight()
            if serve:
                self.server, _ = start_server(self.root)
                print(f"Flight recorder: http://127.0.0.1:{self.server.server_port}", flush=True)
            self.prepare()
            self.build()
            self.edge()
            for index in range(workers):
                self.worker(index)
            self.worker(0, offline=True)
        except (Exception, KeyboardInterrupt) as exc:
            failure = f"{type(exc).__name__}: {exc}"
            self.recorder.event("blocked", status="blocked", reason=failure)
        finally:
            try:
                cleanup_errors = down(self.root, self.recorder) if (self.root / "resources.json").exists() else []
            except Exception as exc:
                cleanup_errors = [f"Cleanup failed: {type(exc).__name__}: {exc}"]
            if cleanup_errors:
                failure = (failure or "") + "; cleanup incomplete: " + "; ".join(cleanup_errors)
            shutil.rmtree(self.root / "secrets", ignore_errors=True)
            if (self.root / "secrets").exists():
                failure = (failure or "") + "; ephemeral secret removal failed"
            if not failure:
                try:
                    from cleanroom.verify import verify
                    result = verify(self.root, require_closure=False)
                    write_json(self.root / "verification.json", result)
                except Exception as exc:
                    failure = f"Independent verification failed: {type(exc).__name__}: {exc}"
            write_json(self.root / "closure.json", {"status": "blocked" if failure else "completed",
                       "reason": failure, "cleanup_errors": cleanup_errors})
            self.recorder.event("closure", status="blocked" if failure else "completed", evidence="closure.json")
            report(self.root)
            if self.server:
                self.server.shutdown()
                self.server.server_close()
        if failure:
            raise RuntimeError(failure)
        return result


def down(root, recorder=None):
    """Remove only objects with this run's exact identity and ownership label."""
    root = Path(root)
    state_path = root / "resources.json"
    if not state_path.is_file():
        return []
    state = json.loads(state_path.read_text())
    resource_names(state["run_id"])
    if state["engine"] not in ("docker", "podman"):
        raise ValueError("unsupported engine in state")
    if state["engine"] == "docker":
        context = subprocess.run(["docker", "context", "inspect"], capture_output=True, text=True, timeout=15)
        if context.returncode:
            return ["Cannot verify local Docker context; resources preserved"]
        endpoint = os.environ.get("DOCKER_HOST") or json.loads(context.stdout)[0]["Endpoints"]["docker"]["Host"]
        if not local_endpoint(endpoint):
            return ["Remote engine rejected; resources preserved"]
    elif any(os.environ.get(name) for name in ("CONTAINER_HOST", "CONTAINER_CONNECTION")):
        return ["Remote Podman settings rejected; resources preserved"]
    errors = []
    for kind, identity in [("container", x) for x in dict.fromkeys(state["containers"])] + [("network", state["network"])]:
        if not re.fullmatch(re.escape(state["run_id"]) + r"-[a-z0-9-]+", identity):
            raise ValueError("resource outside this run")
        result = subprocess.run([state["engine"], kind, "inspect", identity], capture_output=True, text=True, timeout=15)
        if result.returncode:
            if "No such" in result.stderr or "not found" in result.stderr:
                continue
            errors.append(f"Cannot inspect {identity}: {result.stderr.strip()}")
            continue
        info = json.loads(result.stdout)[0]
        labels = info.get("Config", {}).get("Labels", {}) if kind == "container" else info.get("Labels", {})
        if labels.get("cleanroom.run") != state["run_id"]:
            errors.append(f"Ownership mismatch for {identity}; preserved")
            continue
        args = [state["engine"], kind, "rm"] + (["-f"] if kind == "container" else []) + [identity]
        removed = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if removed.returncode:
            errors.append(f"Could not remove {identity}: {removed.stderr.strip()}")
        else:
            # Confirm absence after mutation, not merely command success.
            remaining = subprocess.run([state["engine"], kind, "inspect", identity], capture_output=True, text=True, timeout=15)
            if remaining.returncode == 0:
                errors.append(f"Resource still present: {identity}")
    if recorder:
        recorder.event("cleanup", status="failed" if errors else "completed", errors=errors)
    write_json(root / "cleanup.json", {"errors": errors})
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "verify", "down", "contract", "serve"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--workers", type=int, choices=range(2, 5), default=2)
    parser.add_argument("--documents", type=int, default=4)
    parser.add_argument("--engine", choices=("docker", "podman"), default=os.environ.get("CLEAN_ROOM_ENGINE", "docker"))
    parser.add_argument("--no-ui", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    if not 1 <= args.documents <= 100:
        parser.error("documents must be between 1 and 100")
    latest = TEMPLATE / "output/clean-room-latest.json"
    if args.action == "contract":
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(TEMPLATE / "tests"), "-v"], cwd=TEMPLATE)
        return result.returncode
    if args.action == "run":
        root = args.root or TEMPLATE / "output" / ("clean-room-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3))
        if root.exists() and any(root.iterdir()):
            parser.error("run root must be empty; previous evidence is never overwritten")
        write_json(latest, {"root": str(root.resolve())})
        lab = Lab(root, args.engine)
        try:
            lab.run(args.workers, args.documents, not args.no_ui)
        except Exception as exc:
            print(f"BLOCKED: {exc}\nEvidence: {root}", file=sys.stderr)
            return 1
        print(f"Verified Clean Room: {root}")
    else:
        root = args.root or Path(json.loads(latest.read_text())["root"])
        if args.action == "down":
            errors = down(root)
            shutil.rmtree(root / "secrets", ignore_errors=True)
            return int(bool(errors))
        if args.action == "verify":
            from cleanroom.verify import verify
            try:
                result = verify(root)
            except Exception as exc:
                print(f"NOT VERIFIED: {exc}", file=sys.stderr)
                return 1
            write_json(root / "verification.json", result)
            print(json.dumps(result))
        if args.action == "serve":
            server, _ = start_server(root)
            print(f"Recorded flight: http://127.0.0.1:{server.server_port}", flush=True)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                server.shutdown()
                server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
