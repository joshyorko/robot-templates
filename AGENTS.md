# Repository Guidelines

## Project Structure & Module Organization

This repository publishes RCC robot templates. `templates.yaml` is the catalog and release manifest. Numbered directories (`01-python` through `06-python-uv-native`) are distributable templates; each keeps its own `README.md`, `robot.yaml`, `conda.yaml`, `tasks.py`, and optional `devdata/`, `scripts/`, `assets/`, `dashboard/`, or `docker-compose.yml`. Inspect `robot.yaml` before task code: tasks, `devTasks`, `environmentConfigs`, `artifactsDir`, `PATH`, and `PYTHONPATH` define runtime behavior.

## Build, Test, and Development Commands

Run template commands from the template directory unless you pass `-r`.

- `rcc ht vars -r robot.yaml --silent`: resolve and warm the template environment; this is not a task pass.
- `rcc run -r robot.yaml -t RunTask --silent`: smoke the minimal template.
- `rcc run --dev -t SmokeSQLite -e devdata/env-sqlite-producer.json`: smoke a work-item template without external services.
- `docker compose up -d redis mongodb`: start optional services for backend-specific work-item tests.
- `rcc run -r maintenance-robot/robot.yaml --task maintenance --silent`: run dependency maintenance.

`.github/workflows/release.yaml` packages templates on `v*` tags.

## Coding Style & Naming Conventions

Use Python 3 with PEP 8 defaults: 4-space indentation, `snake_case` functions, and grouped imports. Robocorp task entry points use `@task`; RCC task names in `robot.yaml` use PascalCase such as `RunTask`, `Producer`, or `SmokeSQLite`. Template directories follow `NN-python-purpose`. Use 2-space YAML indentation and pin package versions in `conda.yaml`.

Keep `03-python-work-items` and `05-python-action-server-work-items` distinct: the former demonstrates classic `robocorp.workitems`; the latter demonstrates `actions-work-items` and Action Server integration.

## Testing Guidelines

There is no repo-wide pytest suite. Verify the specific template you changed with RCC smoke commands and document the exact command in your PR. Use RCC-managed commands instead of host Python when template dependencies matter. For work-item templates, always pass the stage-specific `-e devdata/...` env file, and prefer File or SQLite smoke tests before Redis, MongoDB, or live adapters. Confirm artifacts under `output/`, but do not commit them.

## Commit & Pull Request Guidelines

History uses short imperative commits, often with Conventional Commit prefixes (`fix:`, `chore:`). PRs should name the affected template, summarize changes, list RCC commands, and identify which backend was tested. Include screenshots or log paths only for browser, dashboard, or UI-facing changes.

## Security & Configuration Tips

Do not commit `.env`, API keys, generated `output/`, local databases, `.zip` bundles, or runtime work-item state. Use sample `devdata/` files for fixtures and real credentials from job env or secrets. In CI, pin RCC and cache `ROBOCORP_HOME` by RCC version plus `robot.yaml` and `conda.yaml` hashes; never commit cache contents.
