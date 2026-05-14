# Consolidated Task Logs Dashboard

This directory contains the optional dashboard for the classic
`robocorp.workitems` producer-consumer-reporter template. It reads task logs and
work item artifacts from `output/` and writes a self-contained HTML dashboard
for local inspection or CI artifacts.

## Expected Inputs

Run the workflow first from `03-python-work-items`:

```bash
rcc run -t Producer -e devdata/env-for-producer.json
rcc run -t Consumer -e devdata/env-for-consumer.json
rcc run -t Reporter -e devdata/env-for-reporter.json
rcc run -t GenerateConsolidatedDashboard -e devdata/env-for-reporter.json
```

The dashboard code expects the FileAdapter workflow to use directory-based
paths:

```text
output/file/producer-to-consumer/work-items.json
output/file/consumer-to-reporter/work-items.json
output/file/reporter/work-items.json
```

For sharded runs, `scripts/start.sh` stores each consumer output under:

```text
output/file/consumer-to-reporter/shard-<id>/work-items.json
```

## What It Builds

- `consolidated_dashboard.html`: browser-readable task and work item summary.
- `consolidated_data.json`: raw structured data used by the dashboard.
- Optional CSV exports for log and work item tables.

## Files

```text
dashboard/
├── log_consolidator.py                 # Parses logs and work item artifacts
├── jinja2_dashboard_generator.py       # Renders the HTML dashboard
├── templates/
│   └── consolidated_dashboard_jinja2.html
└── schema/
    └── logs_schema.sql
```

If you change artifact locations in `devdata/env-for-*.json` or
`scripts/start.sh`, update the path discovery in `log_consolidator.py` at the
same time.
