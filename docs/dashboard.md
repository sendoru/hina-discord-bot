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
- `/memory`: structured-memory list/filter view
- `/memory/{id}`: memory provenance/source-turn detail
- `/summaries`: personal/shared legacy summary state
- `/memory/cursors`: structured extraction cursor/pending state
- `/reconciliation`: shadow reconciliation proposal review
- `/reconciliation/{id}`: old/new memory comparison and extraction context
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

## Memory inspection

The memory pages expose structured items without changing visibility or lifecycle state.

The list supports owner/origin/kind/disclosure/confidence/time/source-content filters. Relationship
evidence and source message IDs are decoded only in the dashboard read model. Memory detail tries
to resolve each source message ID against the currently retained `turns` rows and links to the
correlated trace when available. A missing raw source is displayed as expired bounded retention,
not as missing provenance.

The repository detects optional `status` and `superseded_by` columns on `memory_items`.
They are displayed when present so the #76 lifecycle migration can be absorbed by the dashboard
query/service boundary instead of leaking schema checks into templates.

## Legacy summary and extraction state

`/summaries` reports:

- personal summary `through_id`, latest retained turn, and pending retained turns,
- shared summary `through_id`, latest retained shared call, and pending shared calls,
- per-owner structured-memory item count,
- the corresponding structured extraction cursor when available.

`/memory/cursors` mirrors Store migration semantics. If a scope has no persisted extraction cursor,
the legacy personal summary `through_id` is shown as the effective baseline, but the dashboard
does **not** initialize or write that cursor. Pending counts are computed against currently retained
turns only.

## Reconciliation review

`/reconciliation` is a read-only workbench over `memory_reconciliation_proposals`. The list joins
each proposal with its target/old and new memory items, supports relation/confidence/kind/origin/date
filters, and reports current-filter rollout metrics such as relation counts and relationship-memory
proposal counts.

The **retry suspect** filter is deliberately conservative: it is true only when the target and new
memory items have the same non-empty source-message ID set. Source order does not matter. This is a
review heuristic, not a conclusion that a retry or extractor bug occurred.

Proposal detail compares old/new content and provenance side by side, resolves source message IDs
against bounded raw turns when still available, and follows retained source `turn_id` values into
`extract_memory_items_shadow` and `memory.shadow_extraction` usage rows. Telemetry rotation or raw
retention can make some of this context unavailable; the UI treats that as normal partial evidence.

No approve/reject decision, proposal mutation, supersede operation, or human-review label is stored
by this phase.

## Next phase

The reconciliation workbench is intended to support the #76 lifecycle rollout decision. Dashboard
write actions remain out of scope until authentication/authorization and the audited write framework
are introduced.
