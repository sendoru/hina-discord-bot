# Compact full-lore experiment (`RAG_MODE=full`)

`RAG_MODE=full` is an **opt-in experiment**. The default remains `v1`, and
`off`, `v1`, `shadow`, `v2` are still available for immediate rollback.
Change this through the Dashboard **`/admin/runtime`**, or use the
`RAG_MODE=full` environment value with no DB override. Config changes are
read for every turn; the **compiled document itself is loaded at bot startup**.
Restart after changing `LORE_PATH` or packaged static lore.

## Data and trust boundaries

- Compile reviewed static `lore.jsonl` into compact, deterministic prose at
  startup. Retain `knowledge` (direct experience / public / reported / unknown
  / inference), source time where meaningful, and clear factual uncertainty.
- Explicitly **omit `audience_only` items** from Hina's in-character prompt.
  The review corpus retains them; do not make Hina remember secrets observed
  only by the player. Likewise skip reference-only adaptation/fandom material.
- `COMMUNITY_LORE=false` excludes community reaction guides; even when enabled,
  label them as nonfactual reactions.
- Do **not** send search-oriented keywords, URLs, source locator, claims or
  other JSON metadata in the full prompt. They remain in `lore.jsonl`.
- Cache static lore in the stable instruction prefix just after character
  instructions, **before** changing relationship and runtime facts. Cache hits
  are provider-dependent and are not guaranteed.
- Dynamic admin/runtime knowledge remains on the existing `lore_references`
  **dynamic-only** path with the same `LORE_MAX_ITEMS/CHARS` limits, permissions,
  enabled flags and live edits. Full mode never serializes runtime DB contents
  into the global cached prefix. Structured memory and privacy/egress policy
  are unchanged.
- `LORE_MAX_ITEMS`/`LORE_MAX_CHARS` apply to dynamic knowledge lookup,
  **not** the statically compiled document. They are not hard truncation
  settings for full mode.
- The v2 embedding controller may be initialized at startup, but **no v2
  retrieval/embedding calls run in a full-mode turn**. No extra LLM call or
  tool round is added by full mode.

## Size and token measurement

Offline (no secrets or API calls):

```bash
uv run python -m hina_bot.tooling.compact_lore_report
```

To obtain an actual Gemini count for **only the compact static lore document**:

```bash
uv run --env-file .env python -m hina_bot.tooling.compact_lore_report \
  --count-tokens --model YOUR_GEMINI_ANSWER_MODEL
```

The countTokens figure does **not** include other system instructions,
conversation or dynamic references, and is not a cost quote. For production
comparison, inspect `usage.jsonl`:

- `rag.mode`: active `rag_mode`, `rag_selected_context`.
- `rag.full`: `rag_full_items`, `rag_full_chars`,
  `rag_full_audience_excluded` (content-free metadata).
- `context.size`: `instruction_lore_chars`, `instruction_chars`,
  `request_chars_total`.
- Answer API request: `input_tokens`, `cached_tokens`, `elapsed_ms`
  and total token usage.

Use the same query set with `RAG_MODE=v1` versus `full` to compare
factual correctness, contradictory ages/roles, event chronology, model
ability to ignore unknown/audience-only facts, p50/p95 latency and actual
input/cached/output token costs. Do not assume a prompt cache hit just
because the full document is static.

## Rollback

Use Dashboard `/admin/runtime` to set `RAG_MODE=v1` (legacy retrieval),
or `off` (no lore/runtime-knowledge retrieval) without a deploy.
The `full` rollout does **not** delete or migrate v2.
