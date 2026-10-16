# Template 08 Dakota acceptance

Recorded on 2026-10-16 on the local `dakota-nvidia` Linux amd64 host. No Actions
or core RCC source was changed. Template 07 and PR #6 remain independent.
This is a qualified comparison lab with an open B gate, not full issue #7
acceptance. Keep [PR #8](https://github.com/joshyorko/robot-templates/pull/8) Draft.

| Mode | Local result | Evidence |
| --- | --- | --- |
| A | PASS | Classic task, repeat, alternate truth, deliberate failure, and no-build reuse. |
| B | FAIL | Real import/catalog and three terminal runs; correct baseline/failure, but inputs are only recorded and workload files are missing from API artifacts. |
| C | PASS | Strict provider acquire, stopped-provider warm reuse, separate archive-only acquire, two strict leased executions and exact unknown-signer refusal. |
| D | NOT IMPLEMENTED | Requires Actions #133/#134; C does not count as D. |

The full command returned 1 because B failed. Generated primary evidence is
under the owner worktree's `08-python-action-server-robot-runtime/output/dakota-final/`:
`report.json`, `report.html`, A command/output files, B `mode-b.json`, run API
responses, independently read SQLite records, and C `c-result.json`, logs,
public roots, detached metadata and execution receipts. These generated files
and private homes are excluded from Git.

Workload source digest:
`6367cd8f2d5e41ad96d94950a890732fab63c9fd4275eff3999241cce0be3ba1`.
The ordered source list includes both fixtures and their fixed ground truth.
A/B/C use the same source identity. Default JSON and CSV hashes match across A
and both C executions. B's default run also matches; its alternate-input run
incorrectly repeats that default output instead of the two-invoice truth.

Owner C artifact:
`sha256:1edf9f6e330745b19cd9bf2b6463fb18351ba687963692969c24e8f6f5da96d8`.
Environment specification:
`sha256:b8e5cb4c1f190cf3044fb0e03b6fe3b6587b571c04d78db8d4299178c1c32c9b`.
The exported archive is 105585890 bytes, below the 256 MiB bound. Both receipts
record completed status, exit zero, a lease ID and valid `strict-remote` trust.
The provider process exited zero before warm and archive-only trials.
RCC's archive-acquire receipt also calls its cold acquisition class `provider`;
the recorded command uses `--archive` after the HTTP provider has stopped.

A/C use released RCC v18.19.7, SHA-256
`f8e0394be7f47ee84d6f4cd734822b87b492a4d6102f977f6a18f29453703d48`.
B uses hash-verified Runtime 1.0.2/Core 1.0.1 wheels and the runtime's actual
bundled RCC 18.19.3, SHA-256
`7e588c01751ca2ae15ba13ef67f2f4b7567697a5a8389737059a73936f509428`.
Wheel URLs/hashes and inspected source SHA are in `runtime-lock.json`; installed
source hashes are in the B receipt. Runtime transitive dependencies are not all
hash locked. Host bootstrap uv was 0.13.1; RCC-managed uv and CI bootstrap are
pinned to 0.11.14.

## Commands and verification

From the template directory, the owner ran:

```sh
ACTIONS_RUNTIME_WORK_DIR="$PWD/output/owner-B/action-server" python3 scripts/compare.py --evidence output/dakota-final
ROBOCORP_HOME="$PWD/output/dakota-final/A/home" RCC_HOLOTREE_MODE=private rcc run --dev -t TestWorkload --silent --no-build
```

The first command intentionally reuses the explicitly selected private B cache;
its per-session HOME and datadir remain fresh. It is not a cold B benchmark.
The default `python3 scripts/compare.py` also prepares its own server and state.
No speedup inference is made from these trials.

All 23 tests passed inside RCC's resolved Python 3.12.11 environment. Real
endpoint trials supplement the five fake-transport protocol tests. Owner review
also tested startup with a generated API key beginning with a hyphen, checked
imported source bytes, independently read persisted run records, and required
downloaded artifact contents to match truth. The negative C test rejects
malformed-root errors as evidence of unknown-signer refusal.

RCC wrapping passed for templates 01–06 and 08. The 08 package check verifies
required files, exact workload bytes, and absence of generated/private state.
Existing templates were not changed. See the PR's exact-head CI checks for
public-runner results; local validation does not substitute for CI.

## Existing-fix audit and blockers

[Actions #315](https://github.com/joshyorko/actions/issues/315) tracks meaningful
input forwarding; [#316](https://github.com/joshyorko/actions/issues/316) tracks
durable workload artifact collection. Both include native trial evidence and
the open/recently merged PR audit. No duplicate fix was implemented.

Robot execution functions are unchanged across community `a70993fafc99a9f94041485542d5a720797ca394`,
integration/PR #221 head `48eeaf171e695d9b8ac140ea53239eaeb6eff331`, and PR #308
head `a17a1968b02e5bfd10acd89dba3fed17ca4ee37d`. PR #221 is not a fix for either
issue. Related import-hardening and shared-output infrastructure do not close
these robot acceptance gaps.

## Native team provenance

Session records confirm the owner model `gpt-6.1-sol/high` and all three worker
models `gpt-6-luna/medium`. Each worker independently read the relevant installed
RCC plugin skills. All workers finished their bounded tasks.

| Role | Native thread ID | Lane commit | Integrated commit |
| --- | --- | --- | --- |
| Owner | `01a14413-23bb-7333-9657-469f76a4c880` | Integration branch | PR #8 |
| A workload | `01a14413-d073-7573-a7d9-278d5dc0c37d` | `b85ffc82d39d8bd2359a876d9f4548040d275a45` | `e48fde61c55cc5f45ecf7e7d3fa90c93ad1a01cb` |
| B API | `01a14413-fdab-7d30-9710-68df2b5f0af8` | `a6fe723bb72c6497fc785bbf09238348a0b86d81` | `8adb2901b35699d0a46cab79435d235ef8ce3db8` |
| C artifact | `01a14414-237b-7b82-89bd-cc4f96f5d3db` | `210feca5c9086545e2fff3659a9e85949e2d8444` | `9d87ba70315e4e43d41dfaf4affe8b66c7ddcc4d` |
