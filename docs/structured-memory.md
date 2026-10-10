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

- only non-`relationship` items with matching `kind` are eligible,
- relation confidence must be at least `0.95`,
- high-confidence `duplicate` supersedes the newly extracted duplicate and keeps the existing target
  active,
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
Deferred conflicts and relationship proposals intentionally remain active until a later policy can resolve
them safely.

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
