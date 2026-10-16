# Python Work Items

Fetch-repos producer-consumer-reporter robot using `actions-work-items`.

Use this template when you want a normal RCC robot that reads and writes work
items with:

```python
from actions import workitems

for item in workitems.inputs:
    workitems.outputs.create({"processed": True, "source": item.payload})
    item.done()
```

This template runs as an RCC robot with `robocorp.tasks`, an optional assistant,
and a dashboard. `05-python-action-server-work-items` focuses on Action Server
integration. Both use the `actions-work-items` API and adapters. Redis and
MongoDB clients are pinned separately in `conda.yaml`; the work item package
does not use the `[all]` extra.

## Run From The Robot Root

All commands below assume your shell is in this directory:

```bash
cd 03-python-work-items
```

The `-e devdata/...` env file is required for every local run. It selects the
adapter and the exact input/output queue or FileAdapter directory for that
stage.

For FileAdapter env files, `RC_WORKITEM_INPUT_PATH` and
`RC_WORKITEM_OUTPUT_PATH` point to JSON files ending in `work-items.json`.
This selects the package's direct-file mode for the list-shaped fixtures and
stage outputs. Keep these explicit file paths in generated shard env files too.

## Quick Smoke

Offline integration checks use temporary files and SQLite databases, without
GitHub requests or repository cloning:

```bash
rcc run --dev -t TestWorkItems
```

To include Redis and MongoDB payload, attachment, and success/failure lifecycle
checks, start the local Compose services and supply their test endpoints:

```bash
docker compose up -d --wait redis mongodb
rcc run --dev -t TestWorkItems -e devdata/env-test-services.json
```

Service tests use unique temporary queues. Without the service env file, those
two tests are skipped. MongoDB exercises the DocumentDB adapter locally; this
does not verify an AWS DocumentDB deployment.

The lowest-friction smoke uses SQLite and does not require Redis or MongoDB:

```bash
rcc run --dev -t SeedSQLiteDB -e devdata/env-sqlite-producer.json
rcc run --dev -t CheckSQLiteDB -e devdata/env-sqlite-producer.json
```

That seeds the `fetch_repos` input queue from
`devdata/work-items-in/input-for-producer/work-items.json` and verifies that
the SQLite database exists. The full workflow below also reaches GitHub: the
producer lists repositories and the consumer clones them. Set `GITHUB_TOKEN` or
`GH_TOKEN` if you need private repositories or higher GitHub API limits.

## Run The Workflow

SQLite local workflow:

```bash
rcc run --dev -t SeedSQLiteDB -e devdata/env-sqlite-producer.json
rcc run -t Producer -e devdata/env-sqlite-producer.json
rcc run -t Consumer -e devdata/env-sqlite-consumer.json
rcc run -t Reporter -e devdata/env-sqlite-for-reporter.json
rcc run --dev -t CheckSQLiteDB -e devdata/env-sqlite-for-reporter.json
```

FileAdapter workflow:

```bash
rcc run -t Producer -e devdata/env-for-producer.json
rcc run -t Consumer -e devdata/env-for-consumer.json
rcc run -t Reporter -e devdata/env-for-reporter.json
```

Redis workflow:

```bash
docker compose up -d redis
rcc run --dev -t SeedRedisDB -e devdata/env-redis-producer.json
rcc run -t Producer -e devdata/env-redis-producer.json
rcc run -t Consumer -e devdata/env-redis-consumer.json
rcc run -t Reporter -e devdata/env-redis-reporter.json
```

MongoDB / DocumentDB local workflow:

```bash
docker compose up -d mongodb
rcc run --dev -t SeedDocDB -e devdata/env-docdb-local-producer.json
rcc run -t Producer -e devdata/env-docdb-local-producer.json
rcc run -t Consumer -e devdata/env-docdb-local-consumer.json
rcc run -t Reporter -e devdata/env-docdb-local-reporter.json
```

Useful local UIs from `docker-compose.yml`:

- RedisInsight: http://localhost:5540
- Mongo Express: http://localhost:8081

For the sharded FileAdapter helper script:

```bash
scripts/start.sh 3
```

The argument is the maximum number of consumer shards. The script keeps runtime
env files under `output/file/runtime-env/` and leaves the committed `devdata`
env files unchanged.

## Queue And Backend Boundaries

The logical queue ladder is:

```text
fetch_repos -> fetch_repos_output -> fetch_repos_report -> fetch_repos_done
```

Stage env files make the boundary explicit:

| Stage | FileAdapter env | SQLite env | Redis env | MongoDB / DocumentDB env |
| --- | --- | --- | --- | --- |
| Producer | `devdata/env-for-producer.json` | `devdata/env-sqlite-producer.json` | `devdata/env-redis-producer.json` | `devdata/env-docdb-local-producer.json` |
| Consumer | `devdata/env-for-consumer.json` | `devdata/env-sqlite-consumer.json` | `devdata/env-redis-consumer.json` | `devdata/env-docdb-local-consumer.json` |
| Reporter | `devdata/env-for-reporter.json` | `devdata/env-sqlite-for-reporter.json` | `devdata/env-redis-reporter.json` | `devdata/env-docdb-local-reporter.json` |

Backend boundaries:

- FileAdapter is directory-based local fixture state. It is best for simple
  path debugging and CI artifact inspection.
- SQLite writes `devdata/work_items.db` plus files under
  `devdata/work_item_files`. It is the default local database path.
- Redis and MongoDB / DocumentDB use local Docker services for development.
  Production credentials should come from job environment or secret injection,
  not committed env files.
- Redis uses `RC_REDIS_URL`, including any authentication and database index.
  The Cluster/Sentinel example files require a compatible single proxy endpoint;
  the adapter does not perform native Cluster or Sentinel discovery.
- `devdata/env-yorko-control-room-*.json` files are placeholders only. Replace
  the placeholder token/workspace values outside the template before using that
  adapter.

## First Files To Edit

- `devdata/work-items-in/input-for-producer/work-items.json`: initial org
  payloads for local FileAdapter and seed tasks.
- `tasks.py`: producer, consumer, and reporter task behavior.
- `scripts/fetch_repos.py`: GitHub repository discovery.
- `devdata/env-*.json`: local adapter and queue wiring.
- `dashboard/README.md` and `generate_consolidated_dashboard.py`: dashboard
  generation and log consolidation.

## Files

```text
03-python-work-items/
├── tasks.py                            # producer / consumer / reporter
├── robot.yaml                          # RCC tasks and devTasks
├── conda.yaml                          # RCC tasks + actions-work-items
├── docker-compose.yml                  # Redis, RedisInsight, MongoDB, Mongo Express
├── devdata/
│   ├── env-for-*.json                  # FileAdapter JSON paths
│   ├── env-sqlite-*.json               # SQLite adapter
│   ├── env-redis-*.json                # Redis adapter
│   ├── env-docdb-local-*.json          # MongoDB / DocumentDB adapter
│   └── work-items-in/                  # local producer seed data
├── scripts/
│   ├── seed_sqlite_db.py               # seed SQLite input queue
│   ├── seed_redis_db.py                # seed Redis input queue
│   ├── seed_docdb.py                   # seed MongoDB input queue
│   ├── check_sqlite_db.py              # inspect SQLite queue state
│   ├── combine_workitems.py            # combine sharded FileAdapter outputs
│   └── start.sh                        # sharded FileAdapter run helper
└── dashboard/                          # consolidated task log dashboard
```

## References

- [actions-work-items](https://pypi.org/project/actions-work-items/)
- [RCC recipes](https://github.com/joshyorko/rcc/blob/master/docs/recipes.md)
- [Work item API and adapters](https://github.com/joshyorko/actions/tree/community/work-items)
