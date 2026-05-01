# Python Action Server Work Items

Producer-consumer-reporter template using `actions-work-items`.

This template keeps the Robocorp-shaped API:

```python
from actions import workitems

for item in workitems.inputs:
    with item:
        workitems.outputs.create({"processed": True, "source": item.payload})
```

## Backends

| Backend | Adapter | Needs Docker |
| --- | --- | --- |
| File | `FileAdapter` | No |
| SQLite | `actions.work_items.SQLiteAdapter` | No |
| Redis | `actions.work_items.RedisAdapter` | Yes |
| MongoDB / DocumentDB | `actions.work_items.DocumentDBAdapter` | Yes |

Yorko Control Room adapter examples are intentionally not included here. This
template is for the portable `actions-work-items` adapters.

## Run From The Robot Root

All RCC commands below assume your shell is already in this directory:

```bash
cd 05-python-action-server-work-items
```

If your generated project is named `test-actions`, run `cd test-actions` first.
Once you are inside that directory, env files are referenced as
`devdata/<file>.json`. Do not prefix them with `test-actions/`.

The `-e` flag is required for every workflow task here. It loads the adapter
env JSON for that exact stage. For the file adapter, env paths are directories
that contain `work-items.json`; never point `RC_WORKITEM_INPUT_PATH` or
`RC_WORKITEM_OUTPUT_PATH` at the JSON file itself.

## Quick Smoke

Run the deterministic local smoke first:

```bash
rcc run --dev -t SmokeFile -e devdata/env-for-producer.json
rcc run --dev -t SmokeSQLite -e devdata/env-sqlite-producer.json
```

That checks File and SQLite without external services.

For Redis and MongoDB/DocumentDB:

```bash
docker compose up -d redis mongodb
rcc run --dev -t SmokeRedis -e devdata/env-redis-producer.json
rcc run --dev -t SmokeDocDB -e devdata/env-docdb-local-producer.json
```

Useful local UIs:

- RedisInsight: http://localhost:5540
- Mongo Express: http://localhost:8081

## Run the Workflow

Seed one input item, then run producer, consumer, and reporter. Every command
uses the env file for the stage it is running.

SQLite is the lowest-friction local database path:

```bash
rcc run --dev -t SeedSQLiteDB -e devdata/env-sqlite-producer.json
rcc run -t Producer -e devdata/env-sqlite-producer.json
rcc run -t Consumer -e devdata/env-sqlite-consumer.json
rcc run -t Reporter -e devdata/env-sqlite-for-reporter.json
```

File adapter:

```bash
rcc run --dev -t SeedFile -e devdata/env-for-producer.json
rcc run -t Producer -e devdata/env-for-producer.json
rcc run -t Consumer -e devdata/env-for-consumer.json
rcc run -t Reporter -e devdata/env-for-reporter.json
```

Redis:

```bash
docker compose up -d redis
rcc run --dev -t SeedRedisDB -e devdata/env-redis-producer.json
rcc run -t Producer -e devdata/env-redis-producer.json
rcc run -t Consumer -e devdata/env-redis-consumer.json
rcc run -t Reporter -e devdata/env-redis-reporter.json
```

MongoDB / DocumentDB:

```bash
docker compose up -d mongodb
rcc run --dev -t SeedDocDB -e devdata/env-docdb-local-producer.json
rcc run -t Producer -e devdata/env-docdb-local-producer.json
rcc run -t Consumer -e devdata/env-docdb-local-consumer.json
rcc run -t Reporter -e devdata/env-docdb-local-reporter.json
```

To inspect a queue, use the env file for the queue you want to read:

```bash
rcc run --dev -t ListWorkItems -e devdata/env-for-producer.json
rcc run --dev -t ListWorkItems -e devdata/env-sqlite-producer.json
rcc run --dev -t ListWorkItems -e devdata/env-redis-producer.json
rcc run --dev -t ListWorkItems -e devdata/env-docdb-local-producer.json
```

## Queue Ladder

The env files make each stage explicit:

```text
fetch_repos -> fetch_repos_output -> fetch_repos_report -> fetch_repos_done
```

Producers and consumers create outputs only while processing a reserved input
item. Use `scripts/seed_workitems.py` or the `Seed*` dev tasks to create the
initial input item.

## Files

```text
05-python-action-server-work-items/
├── docker-compose.yml                  # Redis, RedisInsight, MongoDB, Mongo Express
├── tasks.py                            # producer / consumer / reporter
├── robot.yaml                          # RCC tasks and devTasks
├── conda.yaml                          # actions-work-items[all]
├── devdata/
│   ├── env-for-*.json                  # FileAdapter
│   ├── env-sqlite-*.json               # SQLite
│   ├── env-redis-*.json                # Redis
│   └── env-docdb-local-*.json          # MongoDB / DocumentDB
└── scripts/
    ├── seed_workitems.py               # backend-neutral seeding
    └── smoke_workitems.py              # adapter smoke checks
```

## Action Server

When running inside Action Server, this package exposes work item state through
the normal Action Server work item API:

| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/work-items` | GET | List work items |
| `/api/work-items/stats` | GET | Queue statistics |
| `/api/work-items` | POST | Seed a work item |

## References

- [actions-work-items](https://pypi.org/project/actions-work-items/)
- [RCC recipes](https://github.com/joshyorko/rcc/blob/master/docs/recipes.md)
