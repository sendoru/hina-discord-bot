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

| disclosure | same disclosure space | DM -> guild | guild A -> guild B | public guild -> DM |
| --- | --- | --- | --- | --- |
| `local` | full | hidden | hidden | hidden |
| `implicit` | full | implicit | implicit | implicit |
| `reference_gated` | full | full only after owner reference | full only after owner reference | full when `PUBLIC_SERVER_MEMORY_IN_DM=true`, otherwise owner reference only |
| `global` | full | full | full | full |

For a private guild-channel origin, `reference_gated` remains hidden in every other channel, server,
or DM until the owner explicitly references it. The public-server-to-DM shortcut never applies to
private-channel memories.

`reference_gated` does not mean that any person can unlock a memory by mentioning it. The current
speaker must be the same `user_id` that owns the item. This is checked before every other rule.

The one-way public `guild -> DM` exception intentionally preserves the existing
`PUBLIC_SERVER_MEMORY_IN_DM` behavior: information the user already said in a public server can remain
available in their private conversation with Hina without making it automatically visible in another
server.

## Phase 1: storage and privacy foundation

Phase 1 adds the schema, typed policy model, storage API, deletion integration, and privacy matrix
tests. It does not change any response context.

## Phase 2: shadow extraction

After the normal personal-memory summarizer successfully commits a batch, the same pending turn rows
are sent to a separate structured-memory extractor. This is intentionally a shadow path:

- legacy `summaries` / `shared_summaries` still serve every normal response,
- extracted rows are written to `memory_items` only for inspection and tuning,
- the extractor receives the same user-turn batch, bounded causal `context`, and DM Hina replies used
  to interpret short follow-ups; public-server personal summaries still omit Hina replies,
- every extracted item must cite one or more actual `message_id` values from that batch,
- unknown source ids, invalid enums/confidence, overlong content, malformed JSON, and provenance-less
  rows are rejected instead of repaired,
- exact retry duplicates are suppressed,
- extractor/API/parse/storage failures are isolated after the legacy summary commit, so they cannot
  roll back or fail the existing memory path,
- API usage is visible as the `extract_memory_items_shadow` operation, with content-free
  `memory.shadow_extraction` lifecycle events alongside the existing summary telemetry.

The extractor classifies `kind`, `disclosure`, and `confidence`; these classifications are deliberately
not trusted by the response layer yet. Real stored rows can therefore be reviewed before deciding
thresholds, reconciliation rules, or read-path behavior.

## Still out of scope

Shadow extraction still does not:

- replace `summaries` or `shared_summaries`,
- inject structured items into model context,
- detect cross-space references,
- project `implicit` relationship state into prompts,
- reconcile semantically equivalent facts across separate summary batches.

Those steps should be enabled incrementally after shadow classifications have been inspected against
real conversation data.
