# On-demand lore search (`RAG_MODE=tool`, issue #475)

`tool` is an opt-in mode. It replaces static full-lore injection and the
request-text lexical preselection with a **model-selected local `search_lore`
function**. It does not add an embedding call, semantic classifier, or second
model. The existing answer model decides if and how to search.

`RAG_MODE=full` remains the default and is a one-command rollback; `v1` and
`off` remain available. Set `RAG_MODE=tool` through `/config set` or the
runtime dashboard; the runtime DB override takes precedence over `.env`.
Changing `LORE_PATH` still requires a restart to rebuild the static index.

## Request flow

1. The answer receives the usual core identity, character and world instructions,
   conversation context, plus a short instruction describing `search_lore`.
   **The compact static lore is not injected**; `lore_reference` is not
   prepopulated by lexical RAG.
2. The model may call `search_lore({"queries": ["..."], "limit": 4})`.
   A maximum of 3 phrases, each 2–90 characters, is accepted.
3. Local, deterministic ranking searches accepted static lore indexed at startup
   and **currently enabled** admin knowledge read from the DB per call. It uses
   subject/keyword aliases, IDF-weighted whole words, Korean trigrams and basic particle/spacing normalization. Repeated protagonist names are downweighted by document frequency.
   Missing canonical entity metadata is **never an exclusion filter**.
4. Up to 5 nonduplicate results (default 4), constrained by an overall
   6,000-character JSON budget, return a stable ID, content, kind,
   knowledge/awareness, timeline, confidence and available source metadata.
   Audience-only and reference-only records are excluded; community reaction
   material is suppressed by `COMMUNITY_LORE=false`.
5. The model receives the function output in the existing Responses/Interactions
   continuation and creates a natural reply. A second search is allowed; later
   calls return `lore_search_limit_exceeded`. Other local tools remain usable.

The search output is **reference data, not instructions**. It is not sufficient
evidence to create character relationships or attribute an audience-only
observation to the character. An empty result must not be treated as proof
that the referenced subject does not exist.

## Diagnostics and limitations

- `rag.mode`: the selected mode per answer.
- `rag.tool.turn`: emitted for **every** `tool` answer, including `not_called`, with call/re-query/hit/empty/error counts and returned-character totals. A tool not called is an observation, not proof the answer needed lore.
- `rag.tool`: per-call success / empty / invalid / limit status, query count,
  result count, result IDs, serialized result character count, local elapsed ms.
  Query text is intentionally not copied into the usage log; when provider
  interaction storage is enabled, inspect the stored function call as needed.
- `context.size`: static instruction lore characters should be zero in tool
  mode; request chars are not identical to billed tokens.
- Answer usage events: input/output/cached tokens and provider latency. Each
  function call may add a provider continuation; compare **total billed input
  tokens and latency** rather than the base prompt size alone.

Trade-offs: The model may fail to invoke the tool even when lore is required,
and a lexical backend can miss terms despite query rewriting. Inspect both
tool non-use and unsuccessful searches during live rollout. The word/gram
ranker is deliberately small and dependency-free rather than a full Korean
morphological analyzer or full-text server.

## Rollout

Start with `RAG_MODE=tool` on a controlled deployment, inspect `rag.tool`,
answers and cached-token counts, then widen rollout. If factual grounding or
latency regresses, switch to `full` or `v1` immediately without a deploy.
Existing memory selection, URL Context, Google Search, safety policy and
Discord local tools are unaffected.

## Fixed-scope limitations and next diagnostic step

The JSONL schema, records, `compact_lore.py` and full-mode prompt output
are unchanged. The index only derives search-time tokens and aliases. Logs
can show **non-use / empty matches / repeated searches / returned IDs**, but
they cannot automatically infer a poor query or a misread reference: review
stored Gemini interactions and the final answer for those cases. No raw user
search text is persisted in routing telemetry.

Mock-HTTP provider tests cover both Gemini Interactions and GenerateContent
with Google Search, URL Context and `search_lore` enabled together; actual
provider acceptance and live relevance still require a small opt-in rollout.
