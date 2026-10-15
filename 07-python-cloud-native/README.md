# RCC Cloud-Native + .rcca Environment Artifact Template

This is a runnable, **self-hosted RCC v18.19.6** example. It goes beyond
`06-python-uv-native` by showing how to build an environment once, publish its
content-addressed `sha256:` Environment Artifact, acquire it from another
worker, hold an execution lease, reuse it with the provider offline, and
export/import a `.rcca` archive without Robocorp Control Room.

## Quick start

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
