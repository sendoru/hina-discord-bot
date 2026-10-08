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
- `core/hybrid_retrieval.py`: one runtime factual policy — qualifying lexical rows in
  legacy order followed by calibrated semantic-only recall; returns admitted rows only.
- `core/profile_retrieval.py`: deterministic exact/lexical profile path; no semantic call.
- `tooling/retrieval_calibration.py`: live measurement plus offline weighted/RRF
  comparison; experimental fusion variants do not leak into runtime core.
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

Only factual-eligible candidates are considered. #321 makes the complete
`relation_pair` an exact relationship-lane concern. Factual retrieval does not use the
pair as a candidate gate; canonical `entities` may still nominate exact annotated rows
inside the existing experimental ranker. Whether a retrieved fact is sufficient for the
question remains #315.

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

## Runtime selection and thresholds

`HybridConfig.calibration` defaults to `None`: **no semantic calls** until an explicit
backend-matched `SemanticCalibration(reject, strong)` is supplied. No live threshold is
shipped. Runtime policy is intentionally small:

- `lexical_min`: qualifying factual lexical threshold (default 12)
- `semantic_min`: calibrated semantic admission threshold (default 0.5)
- optional `skip_semantic_at_lexical`: disabled until measured
- semantic timeout

There is no runtime fusion enum, semantic/lexical top-K candidate union, weight tuning or
RRF configuration.

Selection:

1. Filter to factual-eligible candidates. Relation-pair metadata is not a factual gate.
2. Profile requests use the separate `rank_profile()` path and never require semantic
   retrieval. An accidental profile call to `HybridRetriever` returns `not_applicable`.
3. Rank the full factual corpus lexically. Rows at or above `lexical_min` are admitted in
   existing lexical order.
4. Unless a measured strong lexical short-circuit fires, embed the natural semantic query
   and score **every factual candidate** in the small local corpus. There is no semantic
   top-K candidate-generation cutoff.
5. Convert cosine to calibrated relevance with the existing piecewise
   `SemanticCalibration`. Semantic rows at or above `semantic_min` that were not already
   admitted lexically are appended in semantic score order.
6. Semantic rejection never deletes or reorders a qualifying lexical row. Weak rows in both
   channels produce zero result.
7. `BundleComposer` later applies item/character budgets and cross-section deduplication;
   score thresholds are retrieval policy, not packing policy.

This policy directly encodes the current requirement: **preserve lexical precision and use
semantic search only for additional recall**. Static/runtime provenance is not a ranking
tier, and stable input order resolves equal semantic scores.

A missing backend/calibration, backend-key mismatch, HTTP failure, timeout or invalid
vector uses thresholded lexical-only fallback. Task cancellation propagates. Minimal
content-free diagnostics expose semantic status, cache hits/misses and elapsed
milliseconds; no production telemetry or usage-log writes are introduced here.

### Offline comparison only

The calibration CLI still compares:

- lexical-only
- runtime lexical-first + semantic recall
- weighted fusion
- RRF

Weighted/RRF helpers live only in `tooling/retrieval_calibration.py`. They exist to test
whether future live measurements justify changing the runtime policy; they are not
runtime-selectable strategies.

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
lists. Optional `retrieval_text`, `anchor_text`, `entities`, `relation_pair` describe
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
  and each offline comparison method's selected ids/scores, positive/hard-negative/unrelated
  hits, unjudged selections and zero-result status;
- split-specific recall@N, hard-negative rejection, labeled profile precision,
  zero-result accuracy and unjudged counts for lexical-only, the runtime policy, weighted and RRF.

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

Review semantic admission vs lexical preservation, profile-path separation, fixture
labels/splits, candidate cache lifecycle and Gemini request format especially carefully. #312 is now present in the
integration base. Cross-component regressions cover resolved self-profile queries,
complete relation pairs, unannotated factual rows and explicit contradictory annotations.

This PR still does not change shared contracts, legacy scoring, production assembly or
runtime storage. #314 owns ambient activation; #315 owns proposition-level evidence
sufficiency/web fallback; #316 owns invocation policy, live result review, production
shadow comparison, telemetry and rollout gates. Before wiring production, explicitly
review calibration for the chosen model/revision/dimensions/query representation and
relevance policy. There is deliberately no production enable switch in this PR.
