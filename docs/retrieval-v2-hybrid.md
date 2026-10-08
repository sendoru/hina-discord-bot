# Retrieval v2 hybrid factual retrieval (#313)

This is an **experimental, opt-in library and live calibration tool**, not a rollout.
Production answer assembly still uses legacy lexical retrieval and packing. Nothing is
added to `Settings`, SQLite overrides, the dashboard, answer provider construction, or
usage logs. #312 entity/relationship grounding remains independent. No contract, corpus,
or legacy lexical scorer changes are required.

## Size and architecture

At base main `0f205ac`, packaged lore has **130 accepted canon rows**, no meme rows and
no explicit `semantic_text`. The reviewed summaries are usable meaning representations.
Runtime world facts and interpretations each support up to 100 rows; actual deployed DB
contents were not inspected. For this scale, normalized vectors and an exhaustive local
dot product are sufficient. No ANN/vector DB, NumPy dependency, or persistent migration.

- `core/semantic_retrieval.py`: `EmbeddingBackend` protocol, normalization, process-local
  candidate cache, local cosine search and explicit piecewise calibration.
- `ai/embedding_backend.py`: Gemini REST adapter, isolated from answer-provider adapters.
- `core/hybrid_retrieval.py`: supplied entity constraint, candidate union, experimental
  fusion, score/character budgets and lexical fallback; returns only `bundle.facts`.
- `tooling/retrieval_calibration.py`: live measurements and comparison, no rollout writes.

`EmbeddingBackend` exposes `cache_key`, `dimensions`, `embed_query(text)` and
`embed_candidates(texts)`. Both return an internal provider-neutral `EmbeddingResult`
with vectors and `EmbeddingUsage` (prompt token count + HTTP request count). Usage is
per operation, not mutable global counters, so concurrent callers do not mix accounting.
The #311 public request/candidate/bundle contracts are unchanged. No Gemini type leaks.
The Gemini adapter uses the repository's existing `httpx` dependency, model
`gemini-embedding-2`, and **768 dimensions** by default. A different Embedding 2 revision
or dimensionality can be supplied explicitly. There is no automatic model substitution.

Official API references checked 2026-10-08:
[embedding guide](https://ai.google.dev/gemini-api/docs/embeddings) and
[REST reference](https://ai.google.dev/api/embeddings).
Embedding 2 uses task instructions in text rather than `taskType`. The adapter uses
search-query and untitled-document formatting. Query requests use `embedContent` once.
Candidate cache misses use **synchronous `batchEmbedContents`**, not aggregated multipart
content and not asynchronous Batch jobs. Each entry has its own model/content/dimension
request. Chunks run sequentially, default **50 inputs**, configurable from 1 to **100**.
The [upstream integration reference](https://reference.langchain.com/python/langchain-google-genai/embeddings/GoogleGenerativeAIEmbeddings/embed_documents)
documents Google's 100-input ceiling; the REST reference documents independent requests,
ordered results and usageMetadata. The default deliberately stays below that ceiling.
HTTP batching does not imply a rate-quota increase or asynchronous Batch pricing.

Response order is guaranteed by the API. The adapter preserves chunk and positional
order, validates exact cardinality and every vector, then returns the complete result.
There are no echoed per-input ids to independently detect a server violating its ordering
contract; tests use distinct vectors to verify our mapping. A partial, missing, extra or
invalid vector in any chunk prevents the entire fill from reaching the cache. No retries
are hidden in the adapter. Dimensions, finite values and nonzero norms are validated,
and normalization is repeated defensively on all backend outputs.

## Input representation and entity boundaries

Candidate text is **only** `candidate.semantic_representation`: explicit `semantic_text`
first, otherwise reviewed summary / runtime content `search_text`. Keywords, subjects,
source labels and raw JSON are not appended. Updating a guard or confidence never turns
an old cached candidate into new evidence: only vectors are cached; current objects and
reference serialization always come from the current candidate snapshot.

The semantic query uses **`visible_text`**, normalized for whitespace, plus authorized
`anchor_text` only when absent. It never reads `retrieval_text`; lexical ranking continues
to use that existing `lore_query` representation unchanged. Calling prefixes remain
natural conversational text rather than triggering lexical field extraction. A blank
visible turn does not fall back to lexical keywords. An anchor already contained after
Unicode normalization, case folding and punctuation/spacing removal is not repeated.
This deterministic builder does not claim to recognize arbitrary paraphrases as equivalent
or drop potentially meaningful negation based on fuzzy overlap.

Only the visible turn and supplied causal anchor are considered: no system prompt,
`hina.md`, world_core, memory dump or channel-history dump. Canonical ids remain gates,
not pseudo-natural-language text. No LLM rewrite. Intent hints are disabled by default;
the CLI can measure a separate intent-prefix variant. This conversational representation
needs new calibration; results from the former lexical-expanded query are not reusable.

Only factual-eligible candidates are considered. #312 now reserves
`required_entities` for a **complete relation evidence pair**, so factual retrieval does
not treat it as a blanket requirement that every candidate carry the full pair metadata.
The existing corpus is only partially annotated and current runtime knowledge has no
canonical entities.

Entity compatibility therefore has three states during migration:

- no candidate entity metadata: unknown, keep it eligible;
- reviewed metadata that is a proper subset of the required pair: partial/background,
  keep it eligible;
- reviewed metadata that explicitly names a contradictory entity set: reject it.

A full/superset match is also eligible. Missing metadata can support retrieval recall but
must not later be mistaken for entity-grounded answer sufficiency; that decision belongs
to #315. `entities` can nominate exact annotated candidates into the union, but membership
alone never proves relevance. No resolver, alias DB, relationship DB, entity-pair
inference or relationship evidence sufficiency is introduced here; #312 supplies the
resolver and exact relation lane.

## Cache and latency

Reuse one `SemanticIndex` per configured backend/process, not one per turn. Its bounded
LRU (2,048 unique meaning texts by default) uses `(backend key, SHA-256 of meaning text)`.
The key includes provider/model/revision/dimensions/document formatting version. Backend
revision is an explicit operator invalidation token for a provider model alias update.
Text edits miss automatically; identical text can share vectors across static/runtime
sources. Keyword/subject-only edits do not require new embeddings. Deleted or disabled
rows disappear from the next supplied snapshot and cannot be returned from the cache.
Old vectors age out. Queries are never cached. A fill lock avoids duplicate concurrent
cold fills. The full cache-miss fill (including every HTTP batch chunk) commits atomically; malformed
results do not poison it.

`await index.warm(candidates)` prewarms without query embedding, outside a turn deadline,
and returns cache hit/miss counts, candidate usage and warm-up latency.
The first lazy search also fills missing candidates, so cold startup is **not** a
query-only call. Subsequent searches with unchanged content embed only the query. The
retriever bounds the entire optional semantic attempt to 30 seconds by default; request
HTTP timeout defaults to 15 seconds. On timeout/failure it returns thresholded lexical
results, without exception text or content logging. Cold initialization should use
`warm()` if a caller cannot afford repeated cold timeouts. Failed fills retry on the next
explicit attempt; persistent cache/backoff infrastructure is deferred. Process restart
requires a new fill. There is no measured live latency in this PR.

## Experimental selection and thresholds

`HybridConfig.calibration` defaults to `None`: **no semantic calls** until explicit
backend-matched `SemanticCalibration(reject, strong)` is supplied. No live threshold is
shipped. Every numeric fusion/lexical default below is provisional, exposed via a frozen
configuration object, and must be reevaluated before #316 production wiring.

Selection:

1. Empty query/corpus or disabled budget returns no factual result. The retriever itself
   does **not** refuse `conversation` intent: once a caller invokes factual semantic
   retrieval, the legacy regex classifier is not allowed to veto the semantic channel.
   Production invocation/gating remains #316.
2. Profile intent uses lexical only and preserves positive deterministic profile-field
   lexical hits. Optional `skip_semantic_at_lexical` permits a caller to skip semantic
   search at an evaluated strong lexical cutoff; it is unset by default.
3. Otherwise combine lexical top-20, semantic top-20 and supplied canonical-entity hits.
4. Convert cosine to 0 below/equal `reject`, 1 above/equal `strong`, and linearly interpolate.
5. Admit a semantic channel contribution at calibrated score >= 0.5, or a lexical channel
   contribution at raw score >= 12. The channels are independent: semantic top-K absence
   means “not scored by that channel”, not negative evidence, and a semantic rejection
   cannot delete a qualifying lexical result.
6. Fusion uses only admitted channel contributions. Weighted fusion falls back to the
   single admitted channel when the other one is absent/rejected; RRF likewise counts
   only admitted ranks. Lexical-first preserves qualifying lexical order and uses semantic
   results to fill recall gaps.
7. Apply the resulting score, then `UsageBudget`'s strict score threshold, serialized
   reference char limit and item limit. Oversized rows are skipped. Zero is normal; slots
   are not filled with arbitrary `score > 0` candidates.

For lexical score normalization, the provisional scale is 24 (clipped at 1). The original
raw lexical order is preserved in lexical-only/profile and the lexical-first priority
partition, including where normalization clips multiple scores. Stable ties use input
order, never source priority. Runtime ownership/world_fact does not outrank static canon.
Existing interpretation/unknown guards and provenance stay attached to selected rows.

| Fusion | Experimental rule | Tradeoff |
| --- | --- | --- |
| `lexical_first` (default) | qualifying lexical partition in original order, then semantic-only recall | Smallest preservation policy; semantic evidence never vetoes a lexical admission |
| `weighted` | weighted average only when **both** channels are admitted; otherwise use the admitted channel unchanged | Scale/weight sensitive; avoids treating missing/rejected semantic evidence as a penalty |
| `rrf` | sum of `(k+1)/(k+rank)` over **admitted** channels, default k=60 | Rank-scale independent; top-K presence alone is not admission |

RRF scores can approach 2; other scores are <=1. Budget thresholds are backend/fusion
scale, not probabilities. The common 0.5 trial cutoff is not claimed to be optimal across
methods. Lexical-first partition priority can produce a non-monotonic score sequence.
No top1/top2 separation is imposed before measurements establish a benefit. The default
is a conservative **implementation baseline, not an empirical winner**.

A missing backend/calibration, backend-key mismatch, HTTP failure, timeout or invalid
vector uses lexical-only fallback with the same migration-safe entity compatibility and
nonzero lexical cutoff (profile intent preserves any positive field hit).
Task cancellation propagates. Minimal content-free result diagnostics expose semantic
status, successful cache hits/misses and elapsed milliseconds; no production telemetry or
usage-log writes. Failed attempts do not report partial cache counts as successful work.

## Run calibration with a real key

From the repository root, using an environment where `GEMINI_API_KEY` is already set:

```bash
uv run python -m hina_bot.tooling.retrieval_calibration --validate-only
uv run python -m hina_bot.tooling.retrieval_calibration \
  --model gemini-embedding-2 --dimensions 768 \
  --compare-intent-hint --output data/retrieval-v2-calibration.json
```

The CLI does not automatically load `.env.local`. Set the environment using your existing
secret-management workflow; never put the key in a CLI argument, fixture, report or PR.
This is an explicitly invoked, billable API evaluation. With 130 unique candidate texts
and 12 cases, the default 50-input batch size makes **3 candidate HTTP batch requests +
12 single query HTTP requests**. Intent comparison adds 12 query requests, not another
corpus fill. Query embeddings are measured once per case/variant and reused across fusion
comparisons. Counts change with unique text count or `--batch-size`. Each new process
starts with an empty cache. The CLI prewarms the corpus before timing queries separately.
No production logs, private conversations or runtime DB are read by the CLI.

A custom reviewed lore JSONL can be supplied with `--lore`, and fixtures with `--cases`.
`--reject` and `--strong` must be supplied together to compare explicit trial thresholds.
`--top-n` changes selection size; default 3 with a 3,200-character budget. The direct
Python API also accepts a prepared static/runtime candidate union. Profile semantics are
still intentionally skipped. Conversation/fact semantic execution is measured whenever
the caller invokes the retriever; deciding whether production should invoke it is deferred
to #316 rather than inherited from the legacy factual regex classifier.

Fixture schema: unique `id`, `split` (`calibration` or `evaluation`), `visible_text`,
explicit `intent`, and disjoint `positive`, `hard_negative`, `unrelated` candidate-id
lists. Optional `retrieval_text`, `anchor_text`, `entities`, `required_entities` describe
already-authorized routing inputs. Optional `lexical_query` overrides only the lexical
representation after routing; `expected_semantic_query` independently checks the natural
representation. The fixture includes expanded lexical hints alongside an unchanged
semantic expectation, and regression tests verify empty/broken lexical input cannot
suppress semantic recall. Labels refer to packaged, reviewed lore; no corpus
rewrite or entity resolution is performed. The 12 curated cases cover first meeting vs
prior knowledge, rest/responsibility vs shopping, weak-overlap paraphrases, lexical
hard negatives, profile lookup and ordinary/unrelated queries.

Without threshold overrides, the CLI fits a **conservative diagnostic trial** on only
the calibration split: reject=max labeled negative cosine, strong=max(max positive,
midpoint between reject and 1). This may sacrifice all positive recall when distributions
overlap. That is a measured failure to review, not an excuse to lower thresholds until
tests pass. The evaluation split is never used to fit thresholds. It remains a small,
correlated development set; broaden it before claiming generalization or rolling out.
If no separating interval exists or any API call fails, the command fails and writes no
new report. A previous output file is not removed; check exit status before consuming it.

Output is schema-version 2 JSON (stdout unless `--output`), with:

- backend key, dimensions, corpus size, corpus/fixture SHA-256, experimental status;
- per query representation: trial thresholds and all fusion settings;
- calibration/evaluation distributions per label: count/min/median/mean/max;
- initial `candidate_warmup`: latency, cache counts, batch promptTokenCount and request count;
- per case: separate lexical/semantic representations, semantic-query character count,
  query promptTokenCount and embedding latency, candidate miss-fill tokens/warm-up latency,
  HTTP request count, labeled cosine values, semantic top ids/cosines, timing/cache counts,
  and each fusion's selected ids/scores, positive/hard-negative/unrelated hits,
  unjudged selections and zero-result status;
- split-specific recall@N, hard-negative rejection, labeled profile precision,
  zero-result accuracy and unjudged counts for lexical-only and the three fusion methods.

Reports contain **the supplied fixture's lexical and semantic query text** for inspection,
plus ids/scores and usage, never credentials or candidate corpus text. This is CLI-only;
no production usage log or #316 telemetry integration is introduced.
Unjudged candidates are not automatically labeled wrong or correct; inspect them before
interpreting precision. Scores above are genuinely measured **only when run with the
real API**. Unit tests use synthetic vectors solely to verify the mechanics. This PR
contains no live distributions, measured latency, or evidence that one fusion wins.

### Actual token and cost accounting

The adapter reads **`usageMetadata.promptTokenCount`** on each query and each synchronous
batch response. A batch count is added once, never multiplied by its number of inputs.
Missing usage is `null` (unknown), not zero or a text-length estimate; a cache hit has zero
new calls/tokens. Unknown usage propagates to the combined total and suppresses total cost
estimation. Synthetic test metadata validates wiring only, not actual Gemini tokenization.
No assumed 100–200-token query estimate is used.

`semantic_query_character_count` measures the builder's natural text. Query prompt tokens
measure the actual API input, including the adapter's task prefix. `query_embedding.latency_ms`
excludes candidate warm-up and cosine search; initial `candidate_warmup.elapsed_ms` includes
cache preparation and all sequential candidate batch calls. `usage.candidate`, `usage.query`
and `usage.total` aggregate a completed run across both query variants without double
counting the prewarm. `request_count` counts HTTP calls, not quota units or embedded texts.
A failed CLI run emits no completed report; these totals are not a billing ledger for
failed/partial runs.

No provider price is hardcoded. Optionally pass the current **synchronous text embedding**
USD price per million input tokens:

```bash
uv run python -m hina_bot.tooling.retrieval_calibration \
  --batch-size 50 --usd-per-million-tokens "$EMBEDDING_USD_PER_MILLION" \
  --output data/retrieval-v2-calibration.json
```

`cost.estimated_usd = usage.total.prompt_token_count * supplied_rate / 1_000_000`.
Without a rate, token counts remain available and `estimated_usd` is null. The report
records the supplied rate and labels it `operator_supplied`; this excludes credits,
taxes and account-specific discounts and does not assume async Batch discounts.

## Next boundaries and review

Review semantic admission vs lexical preservation, fixture labels/splits, candidate cache
lifecycle and Gemini request format especially carefully. #312 is now present in the
integration base. Cross-component regressions cover resolved self-profile queries,
complete relation pairs, unannotated factual rows and explicit contradictory annotations.

This PR still does not change shared contracts, legacy scoring, production assembly or
runtime storage. #314 owns ambient activation; #315 owns proposition-level evidence
sufficiency/web fallback; #316 owns invocation policy, live result review, production
shadow comparison, telemetry and rollout gates. Before wiring production, explicitly
review calibration for the chosen model/revision/dimensions/query representation and
relevance policy. There is deliberately no production enable switch in this PR.
