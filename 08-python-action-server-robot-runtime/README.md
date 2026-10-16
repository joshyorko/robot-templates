# Compare robot execution paths

Validate synthetic invoices through classic RCC, the existing Action Server robot
API, and direct signed RCC Environment Artifacts. The fixture has three invoices
with known classifications and integer totals. An alternate two-invoice fixture
proves that input selection changes both canonical outputs.

This is a local Linux amd64 experiment. It does not implement an Action Server
artifact-backed robot worker. That fourth path remains **NOT IMPLEMENTED** until
[Actions #133](https://github.com/joshyorko/actions/issues/133) and
[#134](https://github.com/joshyorko/actions/issues/134) supply and qualify it.

## Run on a Linux host or development container

Run commands from this template directory. RCC v18.19.7 is required for the
comparison. The driver verifies the released binary's SHA-256 before running.
Python 3.13, uv 0.11.14, OpenSSL, and network access for initial environment/runtime
setup are prerequisites. Use a development container or user-scoped tooling on
Bluefin; no host package layering is needed. The invoice task itself is offline.
Large RCC homes belong on disk, not a small `/tmp` tmpfs.

```sh
rcc run -t ValidateBatch --silent
python3 scripts/compare.py
```

The first command writes `output/report.json` and `output/results.csv`. The second
executes A/B/C independently and writes a timestamped comparison under
`output/comparison/`. Open its `report.html`; `report.json` contains the same data.
CLI/API responses, run records, output files and lease receipts remain beside it.
The driver exits nonzero when any selected mode fails or is blocked. A skipped
mode never counts as full acceptance, and D never counts as implemented.

To investigate one mode, use `python3 scripts/compare.py --modes A`, `--modes B`,
or `--modes C`. `--evidence /absolute/new/directory` selects a new evidence root;
existing roots are not overwritten. `--rcc /absolute/path/to/rcc` selects the
binary, subject to the same version and checksum verification. B can target an
explicit isolated loopback server with `--server-url`; its prerequisite and
identity must be recorded separately.

B installs SHA-256-verified `actions-runtime==1.0.2` and `actions-core==1.0.1`
wheels into a private Python 3.13 venv. `runtime-lock.json` records their URLs and
hashes, the reviewed Actions source revision, and the runtime's bundled RCC
v18.19.3 hash. That server binary is verified before submitting robot tasks.
It is distinct from the host RCC v18.19.7 used by A/C. Transitive runtime
dependencies are resolved at setup and are not all hash locked.

The qualified published runtime currently fails B acceptance. It records the
alternate input but executes the default fixture, and collects logs while
omitting canonical workload files from run artifacts. These defects are tracked
in [Actions #315](https://github.com/joshyorko/actions/issues/315) and
[#316](https://github.com/joshyorko/actions/issues/316). Baseline and deliberate
failure runs do execute. The comparison must keep reporting FAIL until the
input and durable-output checks pass on a newly qualified runtime.

## What the comparison proves

| Mode | Execution | Acceptance |
| --- | --- | --- |
| A | `robot.yaml`, local Holotree, `rcc run` | Fixed truth, repeat outputs, alternate inputs, intentional failure and `--no-build` reuse. |
| B | Existing Action Server import/catalog/run API | Actual run ID, terminal polling, persisted run, input consumption and durable artifacts. Missing runtime behavior is a blocker. |
| C | Direct RCC signed artifact acquire/exec | Separate homes, deployment roots, detached carrier, lease receipt, verified source, provider-down warm reuse and offline archive. |
| D | Same Action Server endpoint with artifact-backed robot worker | NOT IMPLEMENTED; C is not evidence for D. |

Robot means workload. Action Server owns the API, orchestration and run history.
RCC owns environments, artifacts and verification. MCP is an optional tool
invocation protocol; this robot does not need an MCP stdio service.

Source/config/fixtures and checked truth have a separate ordered SHA-256 identity
defined by `invoice_robot.validator.SOURCE_PATHS`. An environment artifact does
not contain the robot source. C verifies and supplies the source separately,
sets input/output paths explicitly, and launches the Python module deliberately
because `env exec` starts in the materialization root.

`conda.yaml` pins the declared environment. This is not a complete lock of all
transitive packages. RCC's immutable artifact identifies the actual resolved
environment; the report retains its digest and receipts. Durations are observed
local trials, not speedup claims or a production readiness claim.

## Edit and test

Start with `devdata/invoices.json`, its fixed `devdata/expected.json`, and
`invoice_robot/validator.py`. Alternate truth is independently checked in
`devdata/expected-alternate.json`. Inputs are synthetic and contain no real PII.
The classification rule sends invoices at or above 100000 cents for review.

```sh
rcc run --dev -t TestWorkload --silent
rcc run -t FailBatch --silent
```

The failure command must exit nonzero and write `output/failure.json`. It removes
the successful canonical outputs. Keep generated caches, keys, archives,
runtime state and outputs out of Git. Do not upload private homes or signing
keys when sharing evidence. Public roots and detached metadata are distinct;
the detached carrier is not itself a guarantee of signed source provenance.

See [robot-templates #7](https://github.com/joshyorko/robot-templates/issues/7)
for the complete experiment and
[RCC documentation #259](https://github.com/joshyorko/rcc/issues/259) for the
separate documentation/site work. Template 07 and its Clean Room PR remain
independent.
