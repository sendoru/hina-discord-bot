# Read-only dashboard foundation

The dashboard is intentionally introduced as an observability surface before it becomes an
administration surface. The first phase does not change memory, runtime configuration, knowledge,
or Discord state.

## Install and run

Install the optional dashboard dependencies:

```bash
uv sync --extra dashboard
uv run hina-dashboard
```

The dashboard reuses the normal runtime database/log paths by default:

```dotenv
DATABASE_PATH=data/hina.sqlite3
USAGE_LOG_PATH=data/logs/usage.jsonl
EVENT_LOG_PATH=data/logs/events.jsonl
```

Dashboard-only overrides are available when a different mounted/read-only view is needed:

```dotenv
DASHBOARD_DATABASE_PATH=
DASHBOARD_USAGE_LOG_PATH=
DASHBOARD_EVENT_LOG_PATH=
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8765
```

Keep `DASHBOARD_HOST=127.0.0.1` unless access is protected by a trusted tunnel or authenticated
reverse proxy. This service is intended to expose private conversation/memory diagnostics in later
phases and does not provide its own authentication yet.

The dashboard currently exposes the following read-only routes:

- `/`: retained telemetry overview and recent traces
- `/traces`: server-side filtered/paginated turn traces
- `/traces/{turn_id}`: correlated stored turn + event/usage/exchange timeline
- `/conversations`: bounded raw-turn inspection with server-side filters
- `/healthz`: database/telemetry source health

The HTML surface is intentionally desktop-oriented and server-rendered. It uses no client-side
application state and does not add new content telemetry.

## Read-only boundary

`AdminRepository` opens SQLite with `mode=ro` and `PRAGMA query_only=ON`. It never constructs
the production `Store`, so starting the dashboard cannot create tables, run migrations, or modify
runtime data.

The production bot remains the only owner of schema migration and writes.

Existing raw-retention behavior is unchanged. The dashboard does not turn `turns` into a permanent
conversation archive; it can only inspect rows that the normal bounded retention policy still keeps.

## Turn correlation

Discord request lifecycle telemetry and LLM usage telemetry already share an opaque `turn_id`.
Newly stored `turns` rows now persist that same ID, allowing later dashboard pages to join:

```text
stored input/reply
    ↕ turn_id
events.jsonl
usage.jsonl
discord-usage.jsonl
```

Old rows are migrated by adding a nullable `turn_id` column. Existing rows remain `NULL`; no
attempt is made to infer historical correlation.

The read-only repository also tolerates a pre-migration database by returning `turn_id = NULL`
without modifying it.

## Telemetry files

`TelemetryReader` reads the active JSONL files and the current three `RotatingFileHandler`
backups in oldest-to-newest order. Malformed JSON lines are ignored so an interrupted final write
does not make the whole snapshot unreadable.

The reader derives `discord-usage.jsonl` from the configured usage-log directory, matching
`UsageLogger`.

Telemetry retention remains file-rotation based. A future UI must report the actual oldest/newest
available timestamps instead of promising a fixed number of retained days.

## Current UI semantics

### Overview

The overview reports the actual retained telemetry window, source-file availability, trace/status
counts, model-tier counts, aggregate exchange token usage, web-search usage, memory post-processing
failures, and recent safe error fingerprints.

Totals come from `discord-usage.jsonl` where possible so per-call rows are not double-counted.

### Traces

A trace combines all retained rows sharing one opaque `turn_id`:

```text
turn.received / turn.completed / turn.failed
        +
usage.jsonl answer/classifier/memory rows
        +
discord-usage.jsonl exchange aggregate
        +
bounded SQLite turn, when still retained
```

Missing sources are expected. Rotation can remove telemetry before a raw turn expires, and bounded
raw retention can remove the stored message before telemetry rotates. The UI shows correlation
availability rather than treating either case as corruption.

### Conversations

The conversations page only queries the existing `turns` table. Search and pagination happen in
SQLite through the read-only repository. Starting the dashboard still does not change
`HISTORY_TURNS` or preserve old messages.

## Next phase

The next dashboard work adds structured memory, legacy summaries, extraction cursors, and
reconciliation-proposal inspection. Write actions remain out of scope until the structured-memory
lifecycle is stable.
