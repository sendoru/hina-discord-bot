# Structured memory foundation

This document defines the privacy model for the structured long-term memory work. The first phase is
storage and policy only: `memory_items` are **not** injected into normal LLM requests yet, so existing
summary/shared-summary behavior remains unchanged.

## Why this exists

The current summaries are scoped strings. They cannot express the difference between remembering a
fact, carrying relationship familiarity across spaces, and deciding whether a fact may be mentioned
in another Discord space. Structured items separate memory content from disclosure policy.

A Discord guild is treated as one disclosure realm even when the user moves between channels in that
guild. `origin_channel_id` is still stored for provenance and channel-level deletion.

## Item fields

Each item stores:

- `user_id`: owner of the memory. Cross-user access is always denied.
- `content`: normalized memory text.
- `kind`: `fact`, `event`, `preference`, `relationship`, `boundary`, or `task`.
- `origin_realm`: `dm:<user>` or `guild:<guild>` where the memory was formed.
- `origin_channel_id`: channel provenance inside that realm.
- `disclosure`: `local`, `implicit`, `reference_gated`, or `global`.
- `source_message_ids`: source Discord message ids for later provenance/auditing work.
- `confidence`: extractor confidence in the range 0..1.

## Disclosure semantics

The policy primitive returns one of three access levels:

- `full`: the memory content may be supplied to the response layer.
- `implicit`: only a derived relationship/interaction signal may cross the space; the original content
  must not be supplied verbatim.
- `hidden`: the item must not enter the response context.

| disclosure | same realm | DM -> guild | guild A -> guild B | guild -> DM |
| --- | --- | --- | --- | --- |
| `local` | full | hidden | hidden | hidden |
| `implicit` | full | implicit | implicit | implicit |
| `reference_gated` | full | full only after owner reference | full only after owner reference | full when `PUBLIC_SERVER_MEMORY_IN_DM=true`, otherwise owner reference only |
| `global` | full | full | full | full |

`reference_gated` does not mean that any person can unlock a memory by mentioning it. The current
speaker must be the same `user_id` that owns the item. This is checked before every other rule.

The one-way `guild -> DM` exception intentionally preserves the existing
`PUBLIC_SERVER_MEMORY_IN_DM` behavior: information the user already said in a public server can remain
available in their private conversation with Hina without making it automatically visible in another
server.

## Phase boundaries

Phase 1 only adds the schema, typed policy model, storage API, deletion integration, and privacy matrix
tests. It does not:

- extract items from conversations,
- replace `summaries` or `shared_summaries`,
- inject structured items into model context,
- detect cross-space references,
- project `implicit` relationship state into prompts.

Those steps should be enabled incrementally after shadow extraction can be inspected against real
conversation data.
