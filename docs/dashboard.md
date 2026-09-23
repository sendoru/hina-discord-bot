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
- `/identity`: speaker identity resolver outcomes and repeat-pattern observability
- `/conversations`: bounded raw-turn inspection with server-side filters
- `/memory`: structured-memory list/filter view
- `/memory/{id}`: memory provenance/source-turn detail
- `/relationships`: target-scope effective implicit relationship profiles
- `/state`: effective memory/chat-log inheritance and manual notes
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

Reference-gated factual recall now uses this same schema. Trace detail shows the detector reason,
terminal status, bounded candidate/relevant counts, selected memory item ids, and authorization reason
without copying factual memory contents into telemetry. The corresponding structured-memory rows use the
`authorized_factual_recall` projection so operators can drill into the selected items through the
existing memory inspector.

### Conversations

The conversations page only queries the existing `turns` table. Search and pagination happen in
SQLite through the read-only repository. Starting the dashboard still does not change
`HISTORY_TURNS` or preserve old messages.

## Search and local-time UX

The dashboard has one display/search timezone. It is resolved as:

1. `DASHBOARD_TIMEZONE`
2. `RUNTIME_TIMEZONE`
3. `Asia/Seoul`

SQLite and telemetry timestamps remain stored in UTC. Dashboard `datetime-local` controls are
interpreted in the configured dashboard timezone and converted to UTC at the query boundary.
Rendered timestamps are converted back to the dashboard timezone.

High-traffic inspection views expose quick local-time windows such as Today, 1h, 24h, 7d and 30d.

`/conversations` and `/memory` expose content search as a primary control rather than hiding it
among metadata filters. Matching fields are shown with a short escaped snippet and highlighted match.
Conversation search covers input, reply and message ID; memory search covers content and source
message IDs.

Conversation results also link to a bounded same-scope context view showing nearby turns around the
selected result. This is a read-only inspection helper and does not expand runtime retention.

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

## Speaker identity observability

`/identity` observes the existing query-time speaker resolver without creating an alias database or
changing memory visibility.

For an identity query, the Discord preflight and the final response share one opaque `turn_id`.
This lets `identity.resolution` events, `identity_resolve` API usage, and the final answer trace be
correlated without storing Discord message IDs in telemetry.

Identity events contain only bounded metadata:

- `resolved` / `ambiguous` / `none` / `blocked`,
- visible and pre-visibility candidate counts,
- policy block reason,
- opaque reference/user groups,
- whether the resolver API was invoked.

The resolver may return a short `reference` span only when it is copied verbatim from the current
request. Python validates that constraint, normalizes the span, and immediately replaces it with a
deployment-local HMAC group before event logging. The Discord token is used only as a secret key
with an identity-observability domain separator; neither the token nor the raw nickname/reference
is written to telemetry. User IDs used for repeat-mapping analysis are grouped the same way.

The dashboard simulates a conservative cache over these opaque groups. A second stable
reference-group -> user-group resolution counts as a potential cache hit. `ambiguous` and `none`
clear the simulated mapping; a different resolved user is counted as a conflict. These numbers are
planning data for #84, not a runtime cache.

Until #84 exists, no identity event is alias-learning evidence. The dashboard still tracks the
`evidence_source` field and surfaces any `assistant_generated` count so a future feedback-loop
regression is visible.

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

### Effective relationship profiles

`/relationships` reproduces the runtime cross-space implicit relationship projection for a selected
target guild/channel. The projection is intentionally target-specific: relationship memories already
FULL in that disclosure space do not contribute to the implicit profile.

The page uses the same shared aggregation contract as request assembly:

- active `relationship` + `implicit` memories only,
- minimum item confidence `0.8`,
- at most the eight newest eligible observations,
- confidence-weighted noisy-OR with `0.85` exponential recency decay,
- six positive-evidence axes: familiarity, comfort, casualness, teasing tolerance,
  support openness, and task orientation.

Each user row shows the resulting 1..4 axis values and can expand the exact memory observations used
for that projection. Missing axes mean no stored positive evidence, never negative evidence.

### Effective memory / recent-context state

`/state` evaluates the configuration that applies to one target guild/channel/user without constructing
the production `Store` or writing migrations.

The page resolves the same global -> server -> channel inheritance used by runtime code and shows:

- automatic-memory mode plus whether the effective mode permits reads and writes,
- recent-channel-context enablement (`on/off`) and capture scope (`all/direct`) as separate chains,
- the final runtime recent-context behavior (`all`, `direct`, or `off`),
- the exact user/server manual notes addressed by that scope,
- all stored memory/chat-log overrides and all non-`config:*` manual notes.

DM targets intentionally report recent-channel context as `off`, matching runtime behavior even when
the underlying global chat-log setting is `on`.

The `notes` table also stores internal configuration markers such as chat-log capture overrides.
Those rows are excluded from the manual-note list and represented through their relevant configuration
view instead, so internal state is not mistaken for prompt-visible user/server notes.

Manual notes shown for a target are configuration-eligible when memory reads are enabled, but the page
does not claim that every request receives them. Per-request context routing may still choose a
current-channel-only request and suppress cross-channel memory.

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

## Internal module boundaries

The dashboard remains part of the same repository as the Discord runtime, but the application boundary is
explicit:

- `dashboard/app.py` is the composition root: settings, read-only repository/telemetry construction,
  templates/static setup, router registration, and health checks.
- `dashboard/routes/` owns FastAPI request/response wiring by domain.
- `dashboard/services/` owns trace, memory, context-state, and reconciliation read models. The legacy
  `DashboardService` name remains as a compatibility facade so callers do not need to change all at once.
- `dashboard/analytics.py` and `dashboard/identity.py` remain pure read-model builders instead of being
  wrapped in unnecessary service classes.
- `dashboard/repository.py` remains the explicit SQLite read boundary. It still opens the database
  read-only and does not construct the production `Store`.

The intended dependency direction is runtime and dashboard code depending on shared contracts/data, not
the dashboard importing Discord transport details. Regression tests reject direct `dashboard -> discord`
imports, dashboard use of the runtime `Store`, and reverse `core -> dashboard/FastAPI` dependencies.

This layout is the extension point for #99-#101: authentication dependencies can be composed at router/app
boundaries, while future audited write services can remain separate from the existing read repository.

