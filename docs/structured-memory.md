# Structured memory rollout

Structured long-term memory is being introduced incrementally so storage/privacy behavior can be inspected
before it affects normal replies. Existing `summaries` / `shared_summaries` remain the active read path.

## Why this exists

The current summaries are scoped strings. They cannot express the difference between remembering a
fact, carrying relationship familiarity across spaces, and deciding whether a fact may be mentioned
in another Discord space. Structured items separate memory content from disclosure policy.

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

Structured extraction now has its own persisted cursor and cadence. By default it processes four
turns at a time (`STRUCTURED_MEMORY_EVERY=4`) while the legacy text summary remains on its independent
eight-turn cadence. This is intentionally a shadow path:

- legacy `summaries` / `shared_summaries` still serve every normal response,
- extracted rows are written to `memory_items` only for inspection and tuning,
- the extractor receives its own fixed-size pending batch, bounded causal `context`, and DM Hina replies
  used to interpret short follow-ups; public-server extraction still omits Hina replies,
- existing databases seed the new extraction cursor from the current legacy summary cursor so #72-era
  turns are not replayed; that baseline is persisted immediately so later summary updates cannot skip a
  failed structured batch,
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

Exact retry suppression deliberately scans active and superseded history. If item storage or proposal
application partially succeeds but the extraction cursor cannot advance, the retry reuses the existing
item/proposal instead of creating another row. Lifecycle application is idempotent and failures leave the
batch pending for retry.

`memory.shadow_extraction` completion telemetry reports both
`applied_reconciliations` and `deferred_reconciliations` without logging memory content.

## Phase 3: owner-DM memory and numeric relationship projection

Structured memory enters the response path in three deliberately different forms:

- In the owner's DM, `structured_owner_memory` contains all structured items owned by that user,
  regardless of origin realm/channel or disclosure. Relationship rows include their numeric evidence.
  Another user's items are never included.
- In a shared space, relationship items that already resolve to `full` may enter
  `structured_relationship_memory` with raw content and evidence. This covers the same disclosure space
  (and any relationship item explicitly marked `global`).
- Cross-space `implicit` relationship items never expose raw `content`. Only a bounded
  `cross_space_relationship` evidence vector reaches the response model.

Each relationship extractor batch records sparse positive evidence on six 1..4 axes:
`familiarity`, `comfort`, `casualness`, `teasing_tolerance`, `support_openness`, and
`task_orientation`. The batch score is evidence from that batch, not a replacement global state. The
response layer considers at most the eight most recent eligible items and combines each axis with item
confidence and exponential recency decay using noisy-OR. Repeated moderate observations can therefore
accumulate gradually, while one batch cannot overwrite the whole relationship profile.

A missing axis means "no positive evidence", not a negative preference. Current user instructions and
explicit boundaries always override the relationship profile. The model is explicitly forbidden from
reconstructing concrete past events, locations, names, or conversation content from numeric evidence.

- `reference_gated` factual content is still not opened in shared spaces, even when the current message
  looks like a reference. Explicit factual recall remains Phase 4.
- Explicit current-channel-only requests suppress only the cross-space projection; relationship memory
  already FULL in the current disclosure space remains available.
- The structured-memory fields are included in routing context-size accounting so model routing sees the
  same dynamic context that request assembly will serialize.

Owner-DM reads, same-space relationship reads, and cross-space relationship aggregation now consume
active items only, so successfully superseded factual observations do not remain simultaneously visible.
Deferred conflicts and relationship proposals intentionally remain active until a later policy can resolve
them safely.

## Still out of scope

Shadow extraction still does not:

- replace `summaries` or `shared_summaries`,
- make structured memory the primary replacement for legacy summaries,
- detect cross-space references,
- automatically resolve `conflicts` or relationship-memory reconciliation proposals.

Those steps should be enabled incrementally after shadow classifications have been inspected against
real conversation data.
