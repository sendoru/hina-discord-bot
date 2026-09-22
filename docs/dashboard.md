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
- `/analytics`: model/search routing and API usage analytics
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

### Context / provenance

Each successfully stored turn may include a bounded `context_provenance` snapshot describing the
context that Python admitted to the answer request. The snapshot is metadata-only: it records section
counts, message/memory identifiers, ownership relation, provenance class, access/projection, visual
reference metadata, and adapter/provider egress decisions, but it does not copy channel messages,
memory contents, lore bodies, or image bytes.

Trace detail renders this as:

- current-channel / cross-channel-memory decisions,
- adapter and final provider-boundary allowed/blocked counts,
- admitted channel/reply/public/lore/visual sources,
- structured-memory item ids with projection/access and current lifecycle state,
- the bounded causal `memory_context` excerpt when that already-retained data can be correlated by
  message id.

DM conversation-history and server personal-recent slices record the exact source message ids without
copying their text. Structured-memory provenance is derived from the same access rules as request
projection and defaults to active Store items only.

If generation fails before the turn can be stored, a content-free `context.provenance` usage event
still records section counts, scope flags, and blocked-row counts for the trace. It deliberately does
not carry source contents or structured-memory text.

The detailed snapshot is bounded independently from provider context. If its source/item cap is
reached, the trace shows omitted counts instead of silently implying that the displayed metadata is
complete.

This schema is also the extension point for #77: reference-gated factual recall can add detector,
candidate, selected-item, and authorization metadata without moving factual memory contents into
telemetry.

### Conversations

The conversations page only queries the existing `turns` table. Search and pagination happen in
SQLite through the read-only repository. Starting the dashboard still does not change
`HISTORY_TURNS` or preserve old messages.

## Responsive/mobile layout

The dashboard uses the same server-rendered HTML on desktop and mobile. No separate mobile app or
JavaScript navigation layer is required.

On narrow screens:

- the sticky section navigation becomes a horizontally scrollable touch row,
- metric cards collapse from six columns to two and then one,
- filter controls collapse from wrapped desktop controls to a two-column and then one-column form,
- metadata and comparison grids collapse to one column where needed,
- wide data tables scroll inside their own table container instead of widening the whole page,
- message/provenance content wraps while code/JSON remains locally scrollable,
- controls use mobile-friendly touch heights and the page respects safe-area insets.

The dashboard remains intended for administrative inspection rather than dense mobile editing, so
large analytical tables keep their column structure and use local horizontal scrolling instead of
hiding fields.

## Routing and usage analytics

`/analytics` reads only retained `usage.jsonl` and `discord-usage.jsonl` telemetry. It does not
persist aggregates or copy prompts/messages into an analytics store.

The page separates several different questions instead of mixing them into one total:

- answer routing: FAST/SMART, deterministic baseline -> final tier, semantic status/level,
  decision source, score margin, components, reasons, and policy,
- classifier overhead: `model_route_classify` calls, tokens, errors, latency, and provider,
- shadow evaluation: retained `model_route_shadow` baseline -> proposed tier/search transitions,
- web routing: baseline/final search modes, lock reason, semantic web need, and actual
  `web_search_used`,
- API usage: operation/model/provider calls, token fields, errors, latency, search calls, and
  empty-response retries,
- daily UTC provider/model breakdown for before/after deployment comparisons.

Operation/model/provider/time filters apply to per-call usage tables. Routing/search always operate
on answer rows; the operation filter deliberately does not hide answer-routing evidence. Model,
provider, and time filters still apply there. Discord exchange aggregates represent whole turns and
therefore use only the time window rather than pretending an operation/provider-specific exchange
total exists.

Token fields are never defaulted to zero when absent. Each aggregate reports known and missing row
counts. `discord-usage.jsonl` also exposes `usage_complete`, and the UI separates complete,
partial, and older/unknown exchange rows. This makes partial provider telemetry visible instead of
silently undercounting it.

No provider price table is embedded in the dashboard. Cost conversion can be added later as a
configurable layer if needed.

## Memory inspection

The memory pages expose structured items without changing visibility or lifecycle state.

The list supports owner/origin/kind/disclosure/confidence/time/source-content filters. Relationship
evidence and source message IDs are decoded only in the dashboard read model. Memory detail tries
to resolve each source message ID against the currently retained `turns` rows and links to the
correlated trace when available. A missing raw source is displayed as expired bounded retention,
not as missing provenance.

Structured memory now has an `active` / `superseded` lifecycle and an optional
`superseded_by` link. Normal bot retrieval uses active rows only, while the dashboard intentionally
queries the full table so operators can inspect superseded history and follow replacement links.
The repository still detects these columns dynamically so older/pre-migration read-only database copies
remain viewable without dashboard-side schema writes.

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

The runtime may now automatically apply the conservative #76 lifecycle policy to eligible
high-confidence `duplicate` / `corrects` proposals. The workbench itself remains read-only: it does
not approve/reject proposals or mutate lifecycle state. Memory detail pages expose the resulting
`status` and `superseded_by` values for audit.

## Next phase

The reconciliation workbench remains the review surface for tuning the automatic threshold and deciding
whether currently deferred relationship/conflict cases ever need stronger lifecycle semantics. Dashboard
write actions remain out of scope until authentication/authorization and the audited write framework
are introduced.
