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

## Quick Start

Run the deterministic local smoke first:

```bash
rcc task run --dev -t SmokeLocalAdapters
```

That checks File and SQLite without external services.

For Redis and MongoDB/DocumentDB:

```bash
docker compose up -d redis mongodb
rcc task run --dev -t SmokeServiceAdapters
```

Useful local UIs:

- RedisInsight: http://localhost:5540
- Mongo Express: http://localhost:8081

## Run the Workflow

Seed one input item, then run producer, consumer, and reporter. SQLite is the
lowest-friction local path:

```bash
rcc task run --dev -t SeedSQLiteDB
rcc task run -e devdata/env-sqlite-producer.json -t Producer
rcc task run -e devdata/env-sqlite-consumer.json -t Consumer
rcc task run -e devdata/env-sqlite-for-reporter.json -t Reporter
```

File adapter:

```bash
rcc task run --dev -t SeedFile
rcc task run -e devdata/env-for-producer.json -t Producer
rcc task run -e devdata/env-for-consumer.json -t Consumer
rcc task run -e devdata/env-for-reporter.json -t Reporter
```

Redis:

```bash
docker compose up -d redis
rcc task run --dev -t SeedRedisDB
rcc task run -e devdata/env-redis-producer.json -t Producer
rcc task run -e devdata/env-redis-consumer.json -t Consumer
rcc task run -e devdata/env-redis-reporter.json -t Reporter
```

MongoDB / DocumentDB:

```bash
docker compose up -d mongodb
rcc task run --dev -t SeedDocDB
rcc task run -e devdata/env-docdb-local-producer.json -t Producer
rcc task run -e devdata/env-docdb-local-consumer.json -t Consumer
rcc task run -e devdata/env-docdb-local-reporter.json -t Reporter
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
