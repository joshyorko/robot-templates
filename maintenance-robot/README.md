# Maintenance Robot

Automated package maintenance for this `robot-templates` repository.

## Scope

This robot updates pinned package versions in template `conda.yaml` files using `maintenance-robot/allowlists/downloads.json`.

Updated targets include:
- `01-python/conda.yaml`
- `02-python-browser/conda.yaml`
- `03-python-work-items/conda.yaml`
- `04-python-assistant-ai/conda.yaml`
- `05-python-action-server-work-items/conda.yaml`
- `06-python-uv-native/conda.yaml`
- `maintenance-robot/conda.yaml`

This robot intentionally does not manage Homebrew, devcontainer lockfiles, or workflow action pins.

## Repository Layout

```text
maintenance-robot/
├── allowlists/
│   └── downloads.json
├── conda.yaml
├── robot.yaml
├── src/maintenance_robot/
│   ├── __init__.py
│   ├── allowlist_loader.py
│   ├── downloads.py
│   ├── pypi_api.py
│   ├── reporter.py
│   └── tasks.py
└── README.md
```

## Run Locally

1. Build/resolve environment:

```bash
rcc ht vars -r maintenance-robot/robot.yaml --json
```

2. Run maintenance:

```bash
rcc run -r maintenance-robot/robot.yaml --task maintenance --silent
```

Report output is written to `maintenance-robot/output/maintenance_report.json`.

## Available Tasks

| Task | Command | Description |
|------|---------|-------------|
| `maintenance` | `--task maintenance` | Full package maintenance run |
| `update-downloads` | `--task update-downloads` | Package updates only |

## Allowlist Notes

`allowlists/downloads.json` entries define:
- package source (`pypi`)
- package name
- target files
- regex patterns with a named `version` capture group

Example pattern:

```json
"patterns": ["robocorp==(?P<version>[0-9]+(?:\\.[0-9]+)+)"]
```
