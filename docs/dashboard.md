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

The foundation currently exposes only `/healthz`. User-facing inspection pages are added in the
next dashboard phase.

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

## Next phase

The next dashboard PR can build read-only pages on these primitives:

- overview and available telemetry window,
- trace list and per-`turn_id` detail,
- bounded raw conversation inspection,
- structured memory, summaries, and reconciliation proposal inspection.

Write actions remain out of scope until the structured-memory lifecycle is stable.
