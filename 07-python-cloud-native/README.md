# RCC Cloud-Native + .rcca Environment Artifact Template

This is a runnable, **self-hosted RCC v18.19.7** example. It goes beyond
`06-python-uv-native` by showing how to build an environment once, publish its
content-addressed `sha256:` Environment Artifact, acquire it from another
worker, hold an execution lease, reuse it with the provider offline, and
export/import a `.rcca` archive without Robocorp Control Room.

## Quick start

### Clean Room on a local Linux amd64 host

Prerequisites: checksum-verified RCC v18.19.7, OpenSSL 3, and a working local
Docker engine. Provision an approved glibc-based Python 3.12 container image
first. Set `CLEAN_ROOM_IMAGE` to its local `sha256:` image ID or digest-pinned
reference. The conductor never pulls images or installs host packages. It
rejects remote Docker endpoints and uses the inspected immutable image ID.

From this directory, run `rcc run -t CleanRoom --silent`. The default is two
online workers, one `network:none` worker, four synthetic PDFs and two
redaction rounds. For a reduced corpus, use
`rcc task script -- python -m cleanroom.control run --documents 2 --no-ui`.

The run prints the loopback flight-recorder URL while active. It writes a
self-contained `output/clean-room-*/report.html` and raw evidence. The server
closes when the run ends. Reopen it with
`rcc task script -- python -m cleanroom.control serve`. Run `rcc run -t Verify`
to independently check the latest run, `rcc run -t Down` to remove only its
labelled resources, and `rcc run -t Contract` for protocol and evidence tests.
`--root` selects a specific saved run for the underlying control CLI.

Only the builder downloads packages. Workers have private `/rcc-home` mounts,
read-only verified source and a trusted deployment-owned detached carrier.
The HTTPS edge shares the loopback provider's container namespace, requires
authentication, publishes no host port and denies admin and trust writes.
Container isolation supplies the execution boundary; RCC is not a sandbox.
Private demo keys are ephemeral and removed before completion. Public site
roots and carrier files remain for offline verification within 24 hours.

This workload renders digital PDF text with PDFium/Pillow into flattened
redacted PNGs and text. It does **not** perform OCR. Verify checks labelled
sensitive glyph pixels, unchanged safe pixels, expected text, byte determinism,
strict acquisition/lease receipts, source/environment identities and archive
limits. A `.rcca` holds the environment, not source or detached attestations.
Artifact signatures do not authenticate the detached provenance/revocation
metadata; that carrier is trusted local deployment input. No provider-only
compromise claim is made. `run-from-bundle` is not used.

The separate headless CI workflow defaults to the tested digest-pinned image.
Override it with a digest-pinned `CLEAN_ROOM_IMAGE` repository variable or
manual `base_image` input. Tesseract, signer rotation, attack
scenarios and fair cold/warm benchmarks are optional, unproven tiers. No
performance or 90-second runtime claim is made.

### Original lifecycle smoke

Run from the template directory, on Linux or macOS:

```sh
cd 07-python-cloud-native
rcc version
rcc run -r robot.yaml -t RunTask --silent
cat output/cloud-native-smoke.json

# Full 3-home proof (requires Bash and jq):
bash scripts/smoke-lifecycle.sh

# Optional storage backend comparison:
RCC_PROVIDER_BACKEND=journal bash scripts/smoke-lifecycle.sh
```

The script creates a unique private directory under `/tmp` and prints its
location. Look at `publish.json`, `strict-rejection.log`,
`online-acquire.json`, `online-exec.json`, `warm-acquire.json`,
`environment.rcca`, and `offline-exec.json`. The independent producer,
online worker and offline worker all use `RCC_HOLOTREE_MODE=private` and
different `ROBOCORP_HOME` directories. Source task output is in
`output/cloud-native-smoke.json` (plus RCC task logs).

The strict trust failure is **intentional**: unsigned local artifacts cannot
pass `--strict-remote`. The script uses `--permissive-local` **only** for
unsigned artifacts on an unauthenticated loopback provider. Production
acquisition must be signed and strict.

## Architecture

```text
robot.yaml + tasks.py     (source, separate from environment)
         |
RCC producer: pinned Python + pinned uv -> Environment Artifact v1
         |                                   |
         |                     detached provenance / SBOM / trust
         v                                   |
rcc cache serve on 127.0.0.1 -> immutable sha256 artifact
         |                                   |
         +---- online worker: verify -> acquire -> leased env exec
         |
         +---- rcc env export -> environment.rcca
                                |
                                +-- offline worker: import -> acquire -> exec
```

A `.rcca` contains the **environment, not the robot's source code**.
`rcc env exec` starts in the materialized environment directory; it does
not copy `tasks.py`. Execute the source automation with `rcc run` or bundle
the reviewed source plus an exported environment:

```sh
# Replace path with the evidence directory printed by the script.
ARTIFACT_ARCHIVE=/tmp/REPLACE_WITH_RUN_DIRECTORY/environment.rcca
rcc robot bundle -r robot.yaml -o cloud-native-bundle.py \
  --artifact-archive "$ARTIFACT_ARCHIVE"
rcc robot run-from-bundle cloud-native-bundle.py -t RunTask
```

RCC adds `environment/artifact.rcca` to that bundle. A separately generated
canonical platform index can be passed with `--artifact-index`. Do **not**
assume one .rcca file is cross-platform: OS, architecture, libc/ABI, catalog
format and RCC compatibility still apply. Bundle replay is a separate
acceptance path and must be verified on the target platform and trust policy.

## First files to edit

- `tasks.py` — real `robocorp.tasks` source entry point and JSON receipt.
- `robot.yaml` — task wiring and artifact output configuration.
- `conda.yaml` — RCC native uv bootstrap and package pins; no `channels`.
- `scripts/smoke-lifecycle.sh` — runnable lifecycle and trust regression.
- [Deployment](docs/deployment.md) — signed remote transport, detached
  carrier, offline bundles, lifecycle repair, capacity and coordinator seam.

Host Python/venv is not needed for the basic `rcc run` path; RCC owns the
declared interpreter. The Bash+`jq` script is a local development verification
tool. It doesn't deploy Kubernetes resources or provide a cloud broker.

Canonical docs: [compatibility](https://github.com/joshyorko/rcc/blob/main/docs/v18-compatibility.md),
[trust](https://github.com/joshyorko/rcc/blob/main/docs/environment-artifact-trust.md),
[provider hosting](https://github.com/joshyorko/rcc/blob/main/docs/holotree.md#hosting-an-environment-artifact-provider).
