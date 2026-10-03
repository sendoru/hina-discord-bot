# Dashboard observability and local admin foundation

The dashboard started as a read-only observability surface. Local admin writes are now opt-in and
flow through an audited command queue that is executed by the owning bot process; the existing
read repository remains read-only.

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
DASHBOARD_ENABLE_WRITES=false
```

Keep `DASHBOARD_HOST=127.0.0.1`. The service still has no authentication. `DASHBOARD_ENABLE_WRITES`
defaults to false and, until authentication is added, write mode refuses non-loopback hosts.

The dashboard currently exposes the following read-only routes:

- `/`: retained telemetry overview and recent traces
- `/traces`: server-side filtered/paginated turn traces
- `/traces/{turn_id}`: correlated stored turn + event/usage/exchange timeline
- `/analytics`: model/search routing and API usage analytics
- `/conversations`: bounded raw-turn inspection with server-side filters
- `/memory`: structured-memory list/filter view
- `/memory/{id}`: memory provenance/source-turn detail
- `/relationships`: target-scope FULL/raw relationships + effective relationship profile
- `/state`: effective memory/chat-log inheritance and manual notes
- `/summaries`: personal/shared legacy summary state
- `/memory/cursors`: structured extraction cursor/pending state
- `/reconciliation`: shadow reconciliation proposal review
- `/reconciliation/{id}`: old/new memory comparison and extraction context
- `/admin/runtime`: queued runtime configuration set/reset + recent action audit
- `/healthz`: database/telemetry source health

The HTML surface is intentionally desktop-oriented and server-rendered. It uses no client-side
application state and does not add new content telemetry.

## Read/write boundary

`AdminRepository` still opens SQLite with `mode=ro` and `PRAGMA query_only=ON`. It never constructs
the production `Store`, so normal inspection paths cannot migrate or mutate runtime data.

When local write mode is explicitly enabled, POST handlers may only append typed requests to the
`admin_commands` queue through `AdminCommandWriter`. The production bot remains the schema owner and
executes queued actions in its own process, so runtime-only side effects (for example recent-buffer
clears or cached runtime settings) stay synchronized with persistent state.

Completed command rows retain action/target/result/error audit metadata but scrub the original payload.
A command that was `running` when the bot stopped is marked failed on restart rather than retried
automatically.

Existing raw-retention behavior is unchanged. The dashboard does not turn `turns` into a permanent
conversation archive; it can only inspect rows that the normal bounded retention policy still keeps.

## Memory, recent-context, and note controls

`/state` remains the effective inheritance/notes inspector and, in local write mode, also exposes
queued controls for memory mode, recent-context mode, user/server notes, recent-buffer clearing, and
automatic-memory purge. Scope IDs are reconstructed and validated server-side before queueing.

Destructive memory purge requires an explicit confirmation checkbox. The bot process executes the
action under the same channel/memory locks and uses the same Store/recent-buffer operations as the
Discord commands.

## Runtime configuration editing

The runtime editor never updates `runtime_config` directly. POST actions append `runtime.set` or
`runtime.reset` commands to the audited queue; the bot process executes them through
`RuntimeSettings.set_text()` / `reset()` and applies the same recent-context side effects used by
Discord `/config` commands.

The page shows persisted DB overrides and whether a key currently falls back to startup configuration.
It intentionally does not reconstruct the bot process's startup fallback value inside the dashboard
process; a separate read-only effective-config surface can add that later.

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

## Observability epochs

Successful analysis-baseline resets append `observability_epochs(id, reset_at)` without copying the
epoch ID into every JSONL row. The dashboard assigns retained telemetry to a generation by timestamp:

- data before the first reset is `legacy`,
- epoch `N` starts at reset `N` and ends immediately before the next reset,
- the newest epoch is the current analysis baseline.

When at least one marker exists, telemetry-based pages default to the current epoch. Overview is always
current-epoch scoped; Traces and Analytics can explicitly select a historical epoch,
`legacy`, or `all`.

This default prevents fields introduced after an older deployment from being silently compared with
rows where those fields did not exist. Selecting `all` is intentionally allowed for coarse historical
inspection, but the UI warns that missing fields may represent schema-generation differences rather
than runtime zero/false values.

Rows without a valid `at` timestamp cannot be assigned to a specific epoch and are excluded from
epoch-scoped views. They remain visible when `all` retained telemetry is selected.

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

### Effective relationship profiles

`/relationships` uses the shared Guild/DM target-scope picker. The selected scope is evaluated with
the same structured-memory access policy as request assembly.

For a **Guild** target, the page shows both sides of the shared-space relationship context:

- **FULL/raw relationship memory**: the newest eight active relationship items whose access resolves
  to `full` in that shared target scope. Their content and evidence are the same bounded items used
  for `structured_relationship_memory`.
- **Cross-space IMPLICIT projection**: active `relationship + implicit` memories from other
  disclosure spaces whose confidence is at least `0.8` and whose evidence is eligible for implicit
  access. At most the eight newest observations are combined with confidence-weighted noisy-OR and
  `0.85` exponential recency decay.

A relationship item cannot be both FULL and an implicit contributor for the same Guild target.
Same-space or `global` items resolve to FULL; eligible cross-space implicit items contribute only
their evidence vector to the projection.

For a **DM** target, owner memory is the private aggregate space. No DM channel ID is required:
the page evaluates each listed user's owner-DM memory independently, which also avoids pretending that
one shared DM channel could apply to every user row. Active relationship memories owned by each user
resolve to FULL and are shown as the relationship subset of `structured_owner_memory`.

Eligible owner relationship evidence is also aggregated into `owner_relationship_profile` with the
same confidence threshold, per-axis newest-eight window, noisy-OR combination, and recency decay used
by the shared-space relationship projection. Unlike `cross_space_relationship`, this is not a privacy
projection: the same owner-DM response already has FULL access to the concrete relationship memories.

Each user row therefore exposes the stored active relationship count, the target-appropriate FULL/raw
view, the effective 1..4 profile axes, and the observations that actually contributed. For Guild
targets those inputs are cross-space IMPLICIT observations; for DM targets they are eligible owner
relationship observations.

The dashboard shows contributor source text for administrative inspection. In Guild runtime context,
cross-space raw text remains hidden and only aggregated axis values are sent. In DM, the contributor
texts are already present through `structured_owner_memory`, while `owner_relationship_profile`
provides the stable aggregate signal. Missing axes mean no stored positive evidence, never negative
evidence.

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

Stored override tables separate **Realm** and **Channel**, while manual notes separate **Kind**,
**Realm**, and **User** alongside their content. A DM realm identifies its owner (`DM · user <id>`);
it does not add a user-level override. Canonical keys remain available in a **Scope key** disclosure.
Unknown keys and notes classified as `other` show their raw keys directly. The shared dashboard
`scopepresenter` parses display components without changing stored keys, note eligibility, or runtime
inheritance. Summaries and extraction cursors use the same Realm/Channel/User components; Memory and
Reconciliation keep their existing Origin and Owner/origin groupings with structured realm/channel
labels. Trace stored turns, conversation results/context, and source turns in Memory/Reconciliation
details use Realm/Channel/User metadata. Relationship origins and admitted structured-memory origins
in Trace also use the shared presenter. Effective State targets and manual-note keys follow the same
format. Canonical keys remain available in disclosures instead of being repeated beside the labels.
Telemetry Scope values in Overview/Traces are scope-type categories (`guild`/`dm`), not canonical keys;
raw event/context JSON retains its original fields for debugging.
On narrow screens, manual-note rows place Realm/User side by side and content below, so long IDs and
note text remain readable without squeezing four columns together.

For DM lookup, the operator normally enters only the user ID. The dashboard discovers retained DM
channel IDs from conversation/summarization rows, structured-memory origins, reconciliation rows, and
channel-level override keys. A single known channel is selected automatically; multiple known channels
are offered as a selector because a shared test database may contain DM channels from more than one bot.
If no channel can be recovered, the UI exposes a manual channel-ID fallback so channel-level overrides
can still be inspected. Legacy URLs that pass `target_channel_id` for a DM remain accepted.

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

The reconciliation table reserves readable widths for identifiers, owner/origin, numeric metadata,
and timestamps. Target/new content uses bounded three-line previews with links to the full memory
items. Narrow viewports scroll within the table instead of compressing metadata into vertical letters.

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
whether currently deferred relationship/conflict cases ever need stronger lifecycle semantics. Dashboard domain write actions are added incrementally on top of the local audited queue. Remote access
and authenticated authorization remain out of scope until the secure deployment/authentication phase.

## Internal module boundaries

The dashboard remains part of the same repository as the Discord runtime, but the application boundary is
explicit:

- `dashboard/app.py` is the composition root: settings, read-only repository/telemetry construction,
  templates/static setup, router registration, and health checks.
- `dashboard/routes/` owns FastAPI request/response wiring by domain.
- `dashboard/services/` owns trace, memory, context-state, and reconciliation read models. Routes receive
  the specific domain service they need rather than a shared compatibility facade.
- `dashboard/analytics.py` remains a pure read-model builder instead of being wrapped in an unnecessary
  service class; its route composes observability-epoch selection directly.
- `dashboard/repository.py` remains the explicit SQLite read boundary. It still opens the database
  read-only and does not construct the production `Store`.

The intended dependency direction is runtime and dashboard code depending on shared contracts/data, not
the dashboard importing Discord transport details. Regression tests reject direct `dashboard -> discord`
imports, dashboard use of the runtime `Store`, and reverse `core -> dashboard/FastAPI` dependencies.

This layout is the extension point for #99-#101: authentication dependencies can be composed at router/app
boundaries, while future audited write services can remain separate from the existing read repository.


### 개요 지표의 관측 범위

Overview의 API calls, tokens, web searches는 현재 epoch의 exchange 로그 합계입니다.
각 지표는 관측 건수와 누락 건수를 함께 표시합니다. 전부 미관측이면 `—`, 일부
미관측이면 부분 합계, 실제 관측된 0이면 `0`입니다. usage만 남은 trace는 exchange
누락에 포함하며 usage 값으로 합계를 대체하지 않습니다. Stored turns는 epoch와 무관한
SQLite 보존 데이터 수입니다.

Memory failures는 `turn.completed`의 실패 횟수 합계와 실패가 있는 trace 수를 구분합니다.
카드는 `/traces?memory_failure=yes`로 연결되며 실제 `memory_failures > 0` 조건을 사용합니다.

날짜와 confidence 필터는 공통 검증을 거칩니다. Confidence는 유한한 `0..1` 범위이며,
시작 값은 끝 값 이하여야 합니다. 오류가 있으면 조회를 수행하지 않고 입력을 유지하며
필드 옆에 한국어 오류 이유를 표시합니다. 잘못된 날짜는 text 입력으로 보존합니다.

적용 필터 칩은 read model의 정규화된 조건을 표시합니다. 파생된 Guild realm이나 무시된
boolean 조건은 표시하지 않습니다. Scope 유형 제거는 해당 Guild·Channel·legacy realm을
함께 제거하며 개별 ID 제거는 나머지 조건을 유지합니다. 조건 제거는 첫 페이지로 돌아갑니다.
Analytics·Summaries·Cursors에도 같은 칩을 제공합니다.

목록에서 상세 화면으로 이동할 때 `return_to`에 필터와 페이지를 전달합니다. 복귀 경로는
해당 목록의 정확한 내부 경로만 허용합니다. 외부 URL, fragment, 제어 문자, 역슬래시,
다른 화면 경로는 기본 목록으로 돌아갑니다.

HTML 상세의 404와 잘못된 페이지·식별자의 422는 공통 오류 화면과 목록 복귀 링크를
제공합니다. HTTP 상태 코드는 유지합니다. `/healthz`, static, JSON을 요청한 클라이언트는
기존 JSON 오류 형식을 유지합니다. 페이지 번호는 1 이상의 정수여야 합니다.

필터에는 항상 보이는 label을 제공하며 기존 Scope picker의 중첩 label을 유지합니다.
현재 메뉴는 `aria-current="page"`로 표시합니다. 첫 Tab은 본문 바로가기이며 데이터 표의
가로 스크롤 영역도 키보드 포커스를 받을 수 있습니다. 입력 오류는 `aria-invalid`와
`aria-describedby`로 연결합니다.

표시 용어: 메뉴·필드명은 기존 영문을 유지하며 설명·빈 결과·입력 오류는 한국어로
제공합니다. `—`는 미관측·미보존·해당 없음, `Unknown`은 상태 판단 불가입니다. 작은 보조
텍스트는 최소 12px로 조정하고 낮은 대비의 보조 색을 밝힙니다. 넓은 CSS 재정리는 포함하지 않습니다.

Trace 상세의 Summary·Stored turn·Context·Timeline 이동 링크는 해당 구간이 있을 때
표시합니다. 600px 이하에서는 Traces·Memory·Reconciliation을 카드로 보여주며 시간·상태·
상세 링크·본문 요약을 먼저 배치합니다. 메타데이터는 펼치기로 보존하고, 600px 초과 및
Analytics 등의 분석 표는 기존 가로 스크롤을 유지합니다.

날짜 입력은 표시 시간대로 변환하되 초·소수 초를 유지합니다. 시간대가 포함된 URL도
빈 날짜 입력으로 바뀌지 않으며 다시 적용할 때 같은 시각으로 조회합니다.

브라우저 검증은 합성 데이터로 재현합니다. Playwright를 별도로 설치한 뒤 다음을 실행합니다.

```bash
python tests/dashboard/check_browser_ui.py --browser /usr/bin/chromium
```

기본 Playwright Chromium을 설치했다면 `--browser`를 생략할 수 있습니다. 픽스처·스크린샷·
결과 JSON은 매번 `/tmp/hina-dashboard-browser-*`에 생성됩니다. 1440·768·390px에서 긴
한국어 본문·식별자, 빈 결과, 부분 데이터, 오류 입력, 목록 복귀와 키보드 동작을 점검합니다.
실제 기기·스크린리더 검증은 별도이며, 밀리초보다 정밀한 날짜는 값을 보존하는 텍스트
입력으로 표시합니다.

보존·재시도·관계 접근·epoch·최근 관측 안내도 한국어 설명을 사용합니다. 상태·필드·
원본 telemetry 식별자는 영문을 유지하며, Trace의 미관측 문맥 수치를 0으로 보정하지 않습니다.

밀리초까지의 날짜는 native 날짜 컨트롤을 유지하고 불필요한 소수 초의 0을 제거합니다.
이보다 정밀한 값만 텍스트 입력으로 표시하므로 컨트롤 변경으로 정밀도를 잃지 않습니다.
