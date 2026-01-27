# Python Action Server Work Items

A producer-consumer automation template using `actions-work-items` for the Sema4.ai Action Server.

## Overview

This template demonstrates the producer-consumer pattern using the [`actions-work-items`](https://pypi.org/project/actions-work-items/) package, which provides work item management integrated with the Sema4.ai Action Server.

### Key Difference from `robocorp-workitems`

| Feature | `robocorp-workitems` | `actions-work-items` |
|---------|---------------------|----------------------|
| Storage | Control Room / Custom Adapters | Action Server SQLite |
| Import | `from robocorp import workitems` | `from actions.work_items import inputs, outputs` |
| Database | Configurable (Redis, MongoDB, SQLite) | Centralized `{datadir}/workitems.db` |
| Management | External | Built into Action Server |

## How It Works

### The Handoff Mechanism

1. **Shared SQLite Database**: Both producer and consumer use the same `workitems.db` file stored in the action server's datadir
2. **Queue Linking**:
   - Producer writes to an output queue (e.g., `default_output`)
   - Consumer reads from that queue as its input queue

### Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│   Producer  │────►│  workitems.db   │────►│  Consumer   │
│   (writes)  │     │  (SQLite)       │     │   (reads)   │
└─────────────┘     └─────────────────┘     └─────────────┘
                            │
                            ▼
                    ┌─────────────┐
                    │  Reporter   │
                    │ (aggregates)│
                    └─────────────┘
```

## Migration from `robocorp-workitems`

### 1. Update `conda.yaml` / `package.yaml`

```yaml
dependencies:
  - pip:
      # OLD
      # - robocorp-workitems>=1.0.0
      # NEW
      - actions-work-items>=0.2.0
```

### 2. Update imports

```python
# OLD
from robocorp import workitems

# NEW
from actions.work_items import inputs, outputs
```

### 3. Update code patterns

```python
# OLD (robocorp-workitems)
for item in workitems.inputs:
    payload = item.payload
    # process...
    workitems.outputs.create(result)
    item.done()

# NEW (actions-work-items)
for item in inputs:
    payload = item.payload
    # process...
    outputs.create(payload=result)
    item.done()
```

### 4. Configure environment variables

```bash
# Producer
export RC_WORKITEM_OUTPUT_QUEUE_NAME=my_queue

# Consumer (reads from producer's output)
export RC_WORKITEM_QUEUE_NAME=my_queue
```

## Usage

### With Action Server

```bash
# Start the action server
action-server start --actions-sync

# The action server exposes:
# - GET /api/work-items - List all work items
# - GET /api/work-items/stats - Queue statistics  
# - POST /api/work-items - Seed a new work item
```

### With RCC (for development)

```bash
# Run the producer (creates work items)
rcc run -t Producer

# Run the consumer (processes work items)
rcc run -t Consumer

# Run the reporter (generates summary)
rcc run -t Reporter
```

### Environment Variables

| Variable | Description | Used By |
|----------|-------------|---------|
| `RC_WORKITEM_QUEUE_NAME` | Queue to read work items from | Consumer, Reporter |
| `RC_WORKITEM_OUTPUT_QUEUE_NAME` | Queue to write work items to | Producer, Consumer |

## Project Structure

```
05-python-action-server-work-items/
├── tasks.py              # Producer/Consumer/Reporter actions
├── robot.yaml            # Task definitions
├── conda.yaml            # Python dependencies
├── README.md             # This file
├── LICENSE               # MIT License
└── devdata/              # Development test data
    └── work-items-in/
        └── input-for-producer/
            └── work-items.json
```

## API Endpoints (Action Server)

When running with the Sema4.ai Action Server, these endpoints are available for debugging and monitoring:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/work-items` | GET | List all work items in the database |
| `/api/work-items/stats` | GET | Get queue statistics |
| `/api/work-items` | POST | Seed a new work item manually |

## Documentation Resources

- [actions-work-items](https://pypi.org/project/actions-work-items/) - Work items for Action Server
- [Sema4.ai Action Server](https://github.com/Sema4AI/actions) - Action Server documentation
- [RCC Documentation](https://github.com/robocorp/rcc/blob/master/docs/README.md) - Runtime environment manager
