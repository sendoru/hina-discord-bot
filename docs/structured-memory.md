# Structured memory

Structured long-term memory is the persistent personal-memory system used by normal replies.
Legacy personal `summaries` are removed on Store startup after their cursor baseline is migrated.
`shared_summaries` remains a separate public-context path for direct guild interactions.

## Why this exists

Structured items separate remembered content from disclosure policy, provenance, lifecycle state,
and relationship evidence. This avoids maintaining a second scoped-string personal-memory source and
lets normal replies, reconciliation, inspection, and future editing operate on the same persistent data.

A public Discord guild channel contributes to a guild-level disclosure space. A private guild channel
is narrower: memories formed there are automatically full only in that same channel. This preserves
the existing `public_at_capture` privacy boundary while still keeping `origin_channel_id` for
provenance and channel-level deletion.

## Item fields

Each item stores:

- `user_id`: owner of the memory. Cross-user access is always denied.
- `content`: normalized memory text.
- `kind`: `fact`, `event`, `preference`, `relationship`, `boundary`, or `task`.
- `origin_realm`: `dm:<user>` or `guild:<guild>` where the memory was formed.
- `origin_channel_id`: channel provenance inside that realm.
- `origin_public_at_capture`: whether the guild channel was public when the source was captured.
- `disclosure`: `local`, `implicit`, `reference_gated`, or `global`.
- `source_message_ids`: source Discord message ids for provenance/auditing.
- `confidence`: extractor confidence in the range 0..1.
- `relationship_evidence`: sparse positive 1..4 evidence vector used only by `relationship` items.
  Supported axes are `familiarity`, `comfort`, `casualness`, `teasing_tolerance`,
  `support_openness`, and `task_orientation`. Missing/zero means no stored positive evidence,
  never dislike or rejection.

## Annotations (memory v2, #449/#451)

Store schema v2 adds `memory_item_annotations`, one row per memory item. It holds regenerable
judgments about the item rather than new facts: when the memory applies (`time_scope`, `stability`,
`event_at`, `recurrence`), how Hina may use it (`usage_mode`, `directive_type`, `audience`,
`importance`), retrieval hints (`slot`, `keywords`), the quoted source (`evidence`, `observed_at`),
and lifecycle markers (`last_confirmed_at`, `confirmations`, `last_used_at`, `proactive_*`).
`memory_items` remains the source of truth and is not altered.

- An annotation applies to one `item_revision`. When an admin edit raises the item revision, the
  annotation is stale until it is re-annotated (`hina-backfill-annotations` or a dashboard edit).
  Stale or missing annotations fall back to the conservative defaults in #449 §4.1, except that a
  stale directive stays a directive until it is re-annotated (principle 6).
- `annotator` records who wrote it (`extractor`, `consolidator`, `backfill`, `admin`); admin edits win.
- The database enforces the cross-field rules: `directive_type` exists exactly for `directive`
  items, `stability` only for `ongoing` items, and `sensitive` items always have `audience=owner`.
- Relationship items are not annotated; they keep the relationship projection path.
- Every path that deletes a memory item (`/forget`, channel/realm/all purge, dashboard permanent
  deletion) deletes its annotation in the same transaction. `hina-reset-conversation` and
  `hina-reset-analysis-data` preserve annotations together with the memory items.

The table is created by `hina-prepare-db` (see [DB initialization](db-initialization.md)). In this
stage the response path does not read it yet; extraction v2 and backfill populate it, and the response
path stays legacy until P2.

`observed_at` and `last_confirmed_at` use the stored turn time (UTC, `YYYY-MM-DD HH:MM:SS`), so
they compare as strings. `event_at` is a local calendar date (`YYYY-MM-DD` or `YYYY-MM`) in
`RUNTIME_TIMEZONE`.

### Extraction v2

The shadow extractor now returns the annotation fields next to each non-relationship item. Each
input turn carries `at_local` and `weekday` in `RUNTIME_TIMEZONE`, so a relative date such as
"다음 주 화요일" becomes an absolute `event_at`. The prompt walks through tense, stability, usage,
audience and importance in that order; `kind` remains only for compatibility.

The extractor, the directive verifier and the backfill accept their rows either in the requested
envelope (`{"items": [...]}`, `{"results": [...]}`) or as a bare array, which some models return
instead. Only the envelope is relaxed: every row is validated as before, and any other shape is
still an invalid response.

The parser never trusts a judgment it cannot check (#449 principle 6):

- `evidence` must be a substring of a cited user utterance (NFKC, case and whitespace ignored).
  Otherwise it is cleared, `evidence_mismatch` is recorded and `ambient` drops to `contextual`.
- A past or completed marker in the evidence of an `ongoing` item records `past_marker_ongoing` and
  drops `ambient` to `contextual`; the time scope itself is left for review.
- Missing judgments fall back to `contextual`/`unknown`; a `boundary` item falls back to a boundary
  directive so a boundary is never lost. Malformed `event_at`/`recurrence` values are dropped.
  `recurrence` is `weekly:TUE 19:00`, `weekly:TUE` when no clock time was given, several weekdays
  such as `weekly:SAT,SUN`, or `monthly:15`. Empty strings count as omitted.
- `planned`/`episodic` items without a date record `undated_event`; P2 expires them 30 days after
  `observed_at`. `recurring` items without a usable `recurrence` record `recurrence_missing`, since
  P2 cannot compute their next occurrence.
- `audience=server` is narrowed to `owner` for sensitive items and for anything not captured in a
  public guild channel.
- An invalid legacy label no longer drops an otherwise valid item (#449 decision 15): an unknown
  `disclosure` (for example `sensitive` copied from `usage_mode`) becomes the narrowest `local`
  (`disclosure_defaulted`), and an unknown `kind` becomes `fact` (`kind_defaulted`). An unknown kind
  on a row with relationship evidence is still rejected, since it may be a relationship.

Directive items get one more small request (`verify_memory_directives`) before the commit. The
verifier sees only the directive items and the user utterances they cite: `keep` marks the item
verified, `temporary` (boundaries only) keeps it verified as a short ongoing boundary, and `reject`
demotes it to `contextual` with `directive_rejected`. A failed or malformed verification keeps the
directive but unverified (`directive_unverified`), so a boundary is not silently dropped. Directives
are not demoted by the evidence or tense checks above; the verifier decides.

Annotations commit in the same transaction as their items and the extraction cursor. A same-space
re-mention of an active item is counted as a confirmation (`confirmations + 1`, latest
`last_confirmed_at`) instead of a new row, and so is an applied `duplicate` reconciliation. A
`corrects` proposal whose new item has the same `slot` as its target is auto-applied from confidence
0.8 instead of 0.95. Automatic reconciliation no longer requires matching `kind` labels: a fact and a
preference describing the same memory may be merged, and only a relationship/non-relationship pair
is refused as `kind_mismatch` (#449 decision 15). Relationship observations are skipped when an active relationship item from the
same channel and visibility, created within 14 days of the batch, already shows every axis at least
as high (#449 decision 18).

### Annotation backfill

`hina-backfill-annotations` annotates active non-relationship memories that have no annotation
and refreshes automated annotations made stale by a later item edit. Run it with the bot stopped;
`--dry-run` prints only counts. With `--yes --offline-confirmed` it backs up the database, then sends
each owner's items in batches (`--batch-size`, default 10; `--limit` caps the total) to the memory
model with the shared field rules.

- The model sees the stored content, its kind, the stored time (local time and weekday), and the
  owner's own source utterances when conversation retention still has them. The same evidence,
  tense and audience checks as extraction apply, so items without surviving sources cannot become
  `ambient` and are flagged `source_unavailable`.
- A `boundary`-kind item stays a boundary directive candidate even if the model chooses otherwise;
  only the verifier can demote it.
- Directives with sources get the verification request; without sources they stay unverified
  directives.
- Rows are written as `annotator=backfill` for the item's current revision. A stale row keeps its
  original observation, confirmation, use and proactive markers.
- Admin annotations are never replaced; stale ones are counted as `stale_admin_review` for the
  dashboard queue. Owners whose memory writes are disabled are skipped and never sent to the model.
- A failed batch writes nothing and is reported by reason: an exception type, `empty_response`,
  `invalid_json` or `no_items` (no row carried an integer `index`). Output never contains memory
  text.

The `memory.shadow_extraction` completed event adds `annotated_items`, `reconfirmed_items`,
`suppressed_relationship_items`, `directive_items`, `verified_directives` and `rejected_directives`.

## Tiered read path (memory v2 P2, #457)

`PERSONAL_MEMORY_READ_MODE` (runtime setting, startup default `legacy`) chooses how owner memory
reaches a response:

| Mode | Provider context | Recorded |
|---|---|---|
| `legacy` | the existing projection below (all FULL items in DM) | nothing new |
| `shadow` | the existing projection | the tiered selection in context provenance and `memory.selection` telemetry |
| `tiered` | `user_memory` and `relationship_profile` only | the same, plus usage after the reply |

Run `hina-backfill-annotations` first, compare with `shadow`, then switch to `tiered`. `legacy` stays
available as the rollback.

The selector (`core/memory_selection.py`) makes no model calls. Permission comes first: an item is
a candidate only when `memory_access` is FULL for the current scope, except for the cross-space
boundaries below. Selecting nothing is normal.

- **Effective status** is computed from the annotation and the current time: ongoing items are
  `current` until their stability window passes since the last confirmation (short 30 days,
  medium 120, long 365, permanent never), then `unverified`; plans are `upcoming`, `just_passed`
  for 14 days after the date, then `past`; dated events are `recent_event` for 14 days, then
  `past_event`; recurring items are `upcoming` with their next date. Undated plans and events
  expire 30 days after observation.
- **Missing or stale annotations** use the quiet reading (#449 principle 6): facts become
  `contextual`, a `boundary` kind stays a boundary directive, and a stale directive stays a
  directive until it is re-annotated.
- **T0 directives**: FULL directives plus cross-space boundaries, at most 6 items / 600 characters,
  ordered explicit > boundary > most recently confirmed.
- **Cross-space boundaries** (decision 16): a verified boundary, or an unannotated legacy
  `boundary` item, applies in every space where its owner speaks. Outside its FULL spaces it is
  rendered under `quiet_boundaries`, which may only stop Hina from raising the topic. `style`
  directives are not carried across spaces.
- **T1 session follow-up** (§7.1): only on the first turn after an 8-hour gap across all spaces,
  when that turn is a greeting, small talk or a bare call. Candidates are plans that just passed,
  plans within 3 days, and recent importance-3 ambient items; at most 2 in DM. Each item may be
  attempted twice per effective status (`proactive_count`), and any `proactive_followup_off`
  boundary disables it. `MEMORY_SESSION_FOLLOWUP=off|dm|all` (default `dm`); with `all`, a server
  follow-up needs a direct call and an ambient, `audience=server` item from the same space, and
  uses at most one.
- **T2 relevant notes**: lexical matching of keywords and content words against the current
  message (and, at half weight, the explicit reply chain). Ambient items need one keyword hit or
  two content-word hits; contextual items need a keyword hit; sensitive items need a keyword in
  the current message itself. Items used in the last 72 hours are skipped unless the message
  names one of their keywords (decision 19). Admitted items are ranked by their hits weighted by
  the inverse document frequency of the matching query word over the owner's candidates, so a
  word that matches most memories ("보리" for a cat owner) counts less than a rare one ("사료");
  the thresholds above stay unweighted. Near-duplicate contents are skipped (character bigram
  similarity), and at most 4 items / 900 characters are kept.
- **T4**: reference-gated items the owner explicitly brought up this turn.
- **Clusters**: one member per cluster among the members visible here (the canonical one when
  visible, otherwise the most recently confirmed).
- Raw relationship memories are never sent (decision 5); `relationship_profile` is the numeric
  owner profile in DM and the FULL plus implicit profile in a server.

**Usage after the reply** (tiered only): once the reply is delivered, each injected note whose
keyword appears in the reply gets `last_used_at` (the 72-hour cooldown). Unannotated legacy items
need two content words instead. A T1 follow-up candidate records `proactive_status` (its effective
status) and `proactive_count`: 2 when the reply used it, otherwise one more attempt, so an unused
follow-up is retried once in the next session. Only these usage columns are written, never the
annotation's judgments, and items without an annotation row are skipped. This metadata is kept
whenever memory was read, including `read_only`; a failure is logged as `memory.usage_failed` and
never affects the reply. `memory.usage` counts used items and follow-ups without content. Session
gaps use the user's latest stored turn in any space.

### recall_memory tool

With `tiered`, memory that was not selected is no longer in the prompt, so the model can look it
up with `recall_memory(query)` (#449 §7 T3, decisions 7 and 22). It is registered only on the
tiered path while the owner's memory reads are allowed, and the read mode is checked again when it
runs.

- It returns at most 6 active non-relationship memories of the current owner that match the query
  lexically, ranked with the same inverse document frequency weighting as T2, with the same `status`, `date`/`noted` and `use` as `user_memory`. `past` and
  `unverified` items are returned with their dates; superseded and retracted items never are.
- It never widens access: only items FULL in the current scope, plus reference-gated items this
  turn's T4 recall already authorized. Being called by the model is not an owner reference.
- At most 2 calls per answer. The description separates it from `get_chat_history`, which reads the
  current channel's original messages.
- Its results stay in this request's function output. The answer is marked in context provenance
  as `memory_lookup` (call count and `{store, item_id}` only), and extraction drops such replies
  just like `history_lookup` ones (principle 10). `memory.recall` telemetry records status, count and
  time, never the query or content.

Search is lexical over the owner's items in Python rather than SQLite FTS5, so it needs no schema
change; P5 replaces it with embeddings.

The rendered `user_memory` holds `directives`, `quiet_boundaries` and `notes` (`text`, `status`,
`date` or `noted`, `use`, and `scope_relation` when the item came from elsewhere); it carries no
ids, kinds or confidence values. The tiered policy (`prompts/integration/structured_memory_tiered.md`)
is attached whenever the owner has any memory, even when nothing was selected, so the instruction
prefix stays stable. Context provenance lists each admitted item as `{store: personal, item_id,
tier, status}` and `memory_selection` counts tiers and exclusion reasons without content.

## Disclosure semantics

The policy primitive returns one of three access levels:

- `full`: the memory content may be supplied to the response layer.
- `implicit`: only a derived relationship/interaction signal may cross the space; the original content
  must not be supplied verbatim.
- `hidden`: the item must not enter the response context.

A memory is in the same disclosure space when it comes from the current DM, the same private guild
channel, or a public channel in the current guild. A private guild memory does not become guild-wide
merely because the target channel belongs to the same server.

The owner's DM is a special private aggregate space: once ownership matches, structured memories from
all of that user's guilds/channels are available there regardless of disclosure. This never permits
another user's memory to enter the DM.

| disclosure | same disclosure space | DM -> guild | guild A -> guild B | owner DM |
| --- | --- | --- | --- | --- |
| `local` | full | hidden | hidden | full |
| `implicit` | full | implicit | implicit | full |
| `reference_gated` | full | full only after owner reference | full only after owner reference | full |
| `global` | full | full | full | full |

Outside the owner's DM, a private guild-channel `reference_gated` item remains gated in every other
channel or server until the owner explicitly references it.

`reference_gated` does not mean that any person can unlock a memory by mentioning it. The current
speaker must be the same `user_id` that owns the item. This is checked before every other rule.

`PUBLIC_SERVER_MEMORY_IN_DM` remains relevant to the legacy summary path while migration is in
progress, but it no longer controls structured-memory access: the owner's DM aggregates all of that
owner's structured memory.

## Phase 1: storage and privacy foundation

Phase 1 adds the schema, typed policy model, storage API, deletion integration, and privacy matrix
tests. It does not change any response context.

## Phase 2: shadow extraction

Structured extraction has its own persisted cursor and cadence. By default it processes four turns at
a time (`STRUCTURED_MEMORY_EVERY=4`).

- extracted rows are written to `memory_items` and are the persistent personal-memory source,
- the extractor receives its own fixed-size pending batch, bounded causal `context`, and DM Hina replies
  used to interpret short follow-ups; public-server extraction still omits Hina replies,
- when an existing database still has the removed personal `summaries` table, Store startup copies each
  missing cursor baseline from `summaries.through_id` with `INSERT OR IGNORE` and then drops that table,
  so already-initialized structured cursors remain authoritative and retained history is not replayed,
- every extracted item must cite one or more actual `message_id` values from that batch,
- `origin_public_at_capture` is derived conservatively from the cited source rows rather than the
  channel's visibility at extraction time; a private/non-exportable source is never upgraded to public,
- unknown source ids, invalid enums/confidence, overlong content, malformed JSON, and provenance-less
  rows are rejected instead of repaired,
- exact retry duplicates are suppressed,
- identical content/kind/disclosure/evidence in the same origin channel with the same
  public-at-capture visibility is suppressed even when it occurs in a later batch;
  different relationship evidence is retained as a separate observation,
- memory candidates now reserve space for relationship and non-relationship items
  before ranking the bounded 24-item extraction context by simple textual overlap;
  overlap never authorizes memory access or decides whether an item is a duplicate,
- a valid `{"items":[]}` result advances the structured cursor, while API failures, empty responses,
  malformed top-level JSON, or storage failures leave the batch pending for retry,
- structured extraction, personal summary, and shared summary run as separate memory tasks so failures
  do not block each other,
- the normal message path still waits for the fixed four-turn batch, but a background stale-tail sweep
  checks hourly by default and may flush 2-3 pending turns once the oldest pending turn is at least eight
  hours old; one-turn tails remain deferred to avoid turning every isolated message into an extraction,
- the stale sweep shares the normal channel/user locks and model-concurrency semaphore, skips scopes whose
  current memory mode does not allow writes, and leaves the cursor unchanged on failure so the next sweep
  can retry,
- API usage is visible as the `extract_memory_items_shadow` operation, with content-free
  `memory.shadow_extraction` lifecycle events alongside the existing summary telemetry.

The extractor classifies `kind`, `disclosure`, and `confidence`; these classifications are deliberately
not trusted by the response layer yet. Real stored rows can therefore be reviewed before deciding
thresholds, reconciliation rules, or read-path behavior.

## Phase 2.6: reconciliation lifecycle

New extraction batches are compared with a bounded set of recent **active** items. In a server,
candidates stay inside the current user's disclosure space. In the owner's DM, candidates may come from
any realm/channel owned by that user. The extractor may propose exactly one relationship for a new item:

- `duplicate`: materially the same fact/preference was extracted again,
- `corrects`: the current turns explicitly correct, replace, or fix an earlier item,
- `conflicts`: both claims cannot comfortably be true, but the current batch does not clearly establish
  which one should replace the other.

Every proposal is still retained in `memory_reconciliation_proposals` for inspection. The first
automatic lifecycle policy is intentionally conservative:

- matching `kind` is required; relationship duplicates are eligible only when
  both items share the exact origin realm, channel, capture visibility, disclosure
  and numeric evidence, while relationship corrections and different evidence
  observations remain review-only,
- relation confidence must be at least `0.95`,
- high-confidence `duplicate` supersedes the newly extracted duplicate and keeps the existing target
  active; exact same-scope canonical-content duplicates can be skipped before insert,
- high-confidence explicit `corrects` supersedes the old target and keeps the new item active,
- `conflicts`, relationship-memory proposals, lower-confidence proposals, and kind mismatches remain
  observation-only.

`memory_items.status` is either `active` or `superseded`. A superseded item keeps its full content,
source provenance, timestamps, and proposal history, and `superseded_by` records the active replacement
or canonical duplicate target. Normal Store retrieval and reconciliation candidate selection return
active items only; audit/dashboard paths can still inspect superseded rows.

Candidate ids are accepted only when they were actually supplied to the model. The Store independently
re-checks ownership and origin rules when applying a lifecycle transition: DM reconciliation may target
any item owned by the current user, while server reconciliation targets only the current disclosure
space. A proposal can therefore never widen memory visibility merely by superseding another item.

Exact retry suppression deliberately scans active and superseded history, but not retracted rows. This
allows a later genuine re-observation to create memory again after an operator retracts an item. If item storage or proposal
application partially succeeds but the extraction cursor cannot advance, the retry reuses the existing
item/proposal instead of creating another row. Lifecycle application is idempotent and failures leave the
batch pending for retry.

`memory.shadow_extraction` completion telemetry reports both
`applied_reconciliations` and `deferred_reconciliations` without logging memory content.

## Phase 3: owner-DM memory and numeric relationship projection

Structured memory enters the response path in several deliberately different forms:

- In the owner's DM, `structured_owner_memory` contains all structured items owned by that user,
  regardless of origin realm/channel or disclosure. Relationship rows include their numeric evidence.
  Another user's items are never included.
- The owner's DM also receives `owner_relationship_profile`, which aggregates eligible active
  owner relationship evidence into the same sparse 1..4 axes used by the shared-space projection.
  This stabilizes the overall relationship/tone signal without replacing the concrete raw relationship
  memories already present in `structured_owner_memory`.
- In a shared space, active non-relationship items that resolve to `full` enter
  `structured_full_memory`. This covers facts/events/preferences/boundaries/tasks from the same
  disclosure space and items explicitly marked `global`, without opening hidden or reference-gated
  cross-space content.
- Relationship items that already resolve to `full` enter `structured_relationship_memory` with raw
  content and evidence. This keeps relationship evidence on its dedicated projection path.
- Cross-space `implicit` relationship items never expose raw `content`. Only a bounded
  `cross_space_relationship` evidence vector reaches the response model.

Each relationship extractor batch records sparse positive evidence on six 1..4 axes:
`familiarity`, `comfort`, `casualness`, `teasing_tolerance`, `support_openness`, and
`task_orientation`. The batch score is evidence from that batch, not a replacement global state. The
response layer considers at most the eight most recent eligible observations independently for
each axis and combines them with item confidence and exponential axis-local recency decay using noisy-OR.
An observation that carries no evidence for one axis therefore neither ages nor evicts evidence for that
axis. Repeated moderate observations can still accumulate gradually, while one batch cannot overwrite the
whole relationship profile.

A missing axis means "no positive evidence", not a negative preference. Current user instructions and
explicit boundaries always override the relationship profile. The model is explicitly forbidden from
reconstructing concrete past events, locations, names, or conversation content from numeric evidence.

- `reference_gated` factual content is still not opened in shared spaces, even when the current message
  looks like a reference. Explicit factual recall remains Phase 4.
- Raw structured-memory rows supplied to the response model include a coarse `scope_relation`:
  `current_channel`, `same_server_other_channel`, or `cross_space`. Raw guild/channel ids are not
  needed by the model for relevance decisions.
- A current-channel-only request is a relevance constraint, not a new privacy boundary. Privacy-safe
  FULL memory and implicit relationship projection remain available so stable preferences, boundaries,
  and relationship tone do not disappear because of a scope-classifier false positive. The response
  policy uses `scope_relation` to prevent other-channel fact/event/task memory from being presented as
  current-channel history.
- The structured-memory fields are included in routing context-size accounting so model routing sees the
  same dynamic context that request assembly will serialize.

Owner-DM reads and relationship aggregation, same-space relationship reads, and cross-space
relationship aggregation consume active items only, so superseded observations do not remain
simultaneously visible.
Deferred conflicts and relationship corrections or evidence-mismatched duplicates
remain active until a later policy can resolve them safely. New same-evidence
relationship duplicates are automatically superseded, preserving the profile
projection because only the existing observation contributes.

## Phase 4: reference-gated factual recall

Shared-space factual recall is now a separate one-turn authorization path rather than an always-injected
projection.

The gate is deliberately conservative:

- the current speaker must own the memory item,
- only active, non-`relationship`, `reference_gated` items are candidates,
- items already FULL in the current disclosure space are not treated as cross-space recall candidates,
- current-channel-only classification does not disable this path by itself; an explicit owner reference
  may still authorize the item, and the response model receives its `scope_relation` for relevance,
- a strong explicit self-reference must be present in the current user turn,
- topical overlap without an explicit self-reference does not open the gate,
- candidate retrieval is bounded to the 24 newest eligible items,
- relevance selection is lexical in v1 and authorizes at most two best-matching items,
- if the current message supplies only one topic anchor and several memories match it, recall is rejected
  as ambiguous rather than choosing the newest memory,
- a bare recall cue such as `기억나?` can open the detector but does not expose any item without a usable
  topic anchor.

Examples accepted by the detector include forms such as `전에 말했던 ...`, `저번에 얘기했던 ...`,
`DM에서 말한 ...`, `내가 말했던 그 ...`, and a standalone `그거 기억나?`. Third-party forms
such as `철수가 전에 말했던 ...` are not treated as the current speaker referencing their own memory.

Selected rows enter the request only through `authorized_factual_memory`, tagged with
`authorization=owner_explicit_reference`. The response model is told that these rows were already
authorized by Python for the current turn; it does not decide whether hidden factual memory should be
opened.

The same decision is visible in trace provenance without copying memory text into telemetry:
`detector_reason`, candidate/relevant counts, selected item ids, authorization reason, and terminal
status are persisted with the bounded context provenance snapshot. Failed turns still emit only the
content-free counts/status through the `context.provenance` lifecycle row.

## Still out of scope

Shadow extraction still does not:

- replace `summaries` or `shared_summaries`,
- make structured memory the primary replacement for legacy summaries,
- replace the conservative lexical reference matcher with embedding/vector retrieval,
- automatically resolve `conflicts` or relationship-memory reconciliation proposals.

Those steps should be enabled incrementally after shadow classifications have been inspected against
real conversation data.
