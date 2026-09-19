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
- API usage is visible as the `extract_memory_items_shadow` operation, with content-free
  `memory.shadow_extraction` lifecycle events alongside the existing summary telemetry.

The extractor classifies `kind`, `disclosure`, and `confidence`; these classifications are deliberately
not trusted by the response layer yet. Real stored rows can therefore be reviewed before deciding
thresholds, reconciliation rules, or read-path behavior.

## Phase 2.6: shadow reconciliation

Before structured memories affect replies, new extraction batches are compared with a bounded set of
recent existing items. In a server, candidates stay inside the current user's disclosure space. In the
owner's DM, candidates may come from any realm/channel owned by that user. The extractor may propose exactly one
relationship for a new item:

- `duplicate`: materially the same fact/preference was extracted again,
- `corrects`: the current turns explicitly correct, replace, or fix an earlier item,
- `conflicts`: both claims cannot comfortably be true, but the current batch does not clearly establish
  which one should replace the other.

These are observation-only proposals. New items are still stored normally, existing items are not edited,
deleted, hidden, or superseded, and proposals are written separately to
`memory_reconciliation_proposals`.

Candidate ids are accepted only when they were actually supplied to the model. The Store independently
verifies ownership and origin rules: DM reconciliation may target any item owned by the current user,
while server reconciliation targets only the current disclosure space. Items written by a
partially failed retry batch are excluded from the next candidate set by source-message provenance, so a
retry cannot accidentally reconcile an item with itself.

This shadow period is intended to measure how often `duplicate`, `corrects`, and `conflicts` are right
before any automatic supersede behavior is enabled.

## Phase 3: owner-DM memory and implicit relationship projection

Structured memory now enters the response path in two deliberately different forms:

- In the owner's DM, `structured_owner_memory` contains all structured items owned by that user,
  regardless of origin realm/channel or disclosure. Another user's items are never included.
- In a server/shared space, cross-space `implicit` relationship items never expose their raw `content`.
  A sufficiently confident relationship item may only produce the bounded signal
  `cross_space_relationship.familiarity=established`.
- `reference_gated` factual content is still not opened in shared spaces, even when the current message
  looks like a reference. Explicit factual recall remains Phase 4.
- Explicit current-channel-only requests suppress the cross-space relationship projection.
- The structured-memory fields are included in routing context-size accounting so model routing sees the
  same dynamic context that request assembly will serialize.

Because DM full-memory reads can surface stale/conflicting shadow items, this PR is intended to remain
draft until reconciliation/supersede behavior is validated and inserted before production rollout.

## Still out of scope

Shadow extraction still does not:

- replace `summaries` or `shared_summaries`,
- inject structured items into model context,
- detect cross-space references,
- apply reconciliation proposals or mark old items as superseded.

Those steps should be enabled incrementally after shadow classifications have been inspected against
real conversation data.
