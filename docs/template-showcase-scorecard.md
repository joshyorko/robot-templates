# Template Showcase Scorecard

This scorecard keeps the showcase pass focused on the six public templates in
`templates.yaml`. The maintenance robot stays repo infrastructure unless the
catalog intentionally promotes it later.

## Acceptance Bar

Each public template should answer these questions from its README:

- What durable automation pattern does this template demonstrate?
- Which directory should the user run commands from?
- What exact `rcc run ...` command proves the template starts?
- Which env files are required, especially `-e devdata/...` files?
- What output or artifact should the user expect?
- Which files should the user edit first?

## Current Template Targets

| Template | Showcase role | Primary RCC skill | Minimum smoke path | Notes |
| --- | --- | --- | --- | --- |
| `01-python` | Minimal RCC robot baseline | `rcc-robots` | `rcc run -t RunTask` | Keep plain Python only. |
| `02-python-browser` | Browser automation with Playwright | `rcc-robots` | `rcc run -t BrowserExample` | Prefer deterministic, unauthenticated browser work. |
| `03-python-work-items` | Classic `robocorp.workitems` producer/consumer/reporter | `rcc-workitems` | File or SQLite producer -> consumer -> reporter ladder | Keep distinct from `actions-work-items`. |
| `04-python-assistant-ai` | Assistant/AI robot with explicit credential boundary | `rcc-robots` | `rcc run --dev -t TestKeys` plus documented assistant run | Do not commit credentials or imply no key is needed. |
| `05-python-action-server-work-items` | Reference-grade Action Server work item template | `action-server`, `rcc-workitems` | Existing backend smoke tasks | Tighten real gaps only; avoid churn. |
| `06-python-uv-native` | RCC robot showing uv-native dependency setup | `rcc-robots` | `rcc run -t RunTask` | Make uv value clear; do not duplicate `01-python`. |

## Integration Checks

- `03-python-work-items` must keep the classic `robocorp.workitems` API.
- `05-python-action-server-work-items` must keep the `actions-work-items` and
  Action Server boundary.
- READMEs should show commands from the template root, not from the workspace
  root, unless `-r path/to/robot.yaml` is explicitly part of the command.
- Sample env files must stay local and credential-free.
- Bluefin host setup should remain boring: no random host package layering.
