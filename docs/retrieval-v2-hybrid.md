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
`embed_candidates(texts)`. No Gemini type reaches a public retrieval contract.
The Gemini adapter uses the repository's existing `httpx` dependency, model
`gemini-embedding-2`, and **768 dimensions** by default. A different Embedding 2 revision
or dimensionality can be supplied explicitly. There is no automatic model substitution.

Official API references checked 2026-10-08:
[embedding guide](https://ai.google.dev/gemini-api/docs/embeddings) and
[REST reference](https://ai.google.dev/api/embeddings).
Embedding 2 uses task instructions in text rather than `taskType`. The adapter uses
search-query and untitled-document formatting. It sends one independent `embedContent`
request per candidate (up to four concurrent), since a multipart Embedding 2 input can
aggregate into one vector. Query search sends exactly one query request. No retries are
hidden in the adapter. Vector count, dimensions, finite values and nonzero norms are
validated, and normalization is repeated defensively on all backend outputs.

## Input representation and entity boundaries

Candidate text is **only** `candidate.semantic_representation`: explicit `semantic_text`
first, otherwise reviewed summary / runtime content `search_text`. Keywords, subjects,
source labels and raw JSON are not appended. Updating a guard or confidence never turns
an old cached candidate into new evidence: only vectors are cached; current objects and
reference serialization always come from the current candidate snapshot.

The query starts with `retrieval_text` (existing routing prefix removal/normalization),
not literal `visible_text`. Whitespace is normalized. An authorized `anchor_text` is
appended only when not already present; a blank query does not fall back to raw input.
Existing routing lexical expansions are retained, not stripped by brittle suffix rules.
Canonical ids are used as constraints, not embedded as pseudo-natural-language aliases.
No per-turn LLM rewrite. Intent hints are disabled by default; the CLI can measure a
second representation with an intent prefix. These variants need separate calibration.

Only factual-eligible candidates are considered. `required_entities` is an ALL-members
constraint applied **before lexical and semantic search**, including every fallback.
Missing metadata fails a supplied constraint; an empty constraint does not invent one.
`entities` can nominate additional exact/entity candidates into the union, but membership
alone never proves relevance. No resolver, alias DB, relationship DB, entity-pair
inference or relationship evidence sufficiency is introduced. No current corpus entity
annotations are fabricated to make this path pass. #312 supplies canonical metadata.

## Cache and latency

Reuse one `SemanticIndex` per configured backend/process, not one per turn. Its bounded
LRU (2,048 unique meaning texts by default) uses `(backend key, SHA-256 of meaning text)`.
The key includes provider/model/revision/dimensions/document formatting version. Backend
revision is an explicit operator invalidation token for a provider model alias update.
Text edits miss automatically; identical text can share vectors across static/runtime
sources. Keyword/subject-only edits do not require new embeddings. Deleted or disabled
rows disappear from the next supplied snapshot and cannot be returned from the cache.
Old vectors age out. Queries are never cached. A fill lock avoids duplicate concurrent
cold fills. Full validated batches commit atomically; malformed results do not poison it.

`await index.warm(candidates)` prewarms without query embedding, outside a turn deadline.
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

1. A conversation intent, empty query/corpus or disabled budget returns no factual result
   and makes no embedding calls. A caller must route a genuinely factual request first.
2. Profile intent uses lexical only. Optional `skip_semantic_at_lexical` permits a caller
   to skip semantic search at an evaluated strong lexical cutoff; it is unset by default.
3. Otherwise combine lexical top-20, semantic top-20 and supplied canonical-entity hits.
4. Convert cosine to 0 below/equal `reject`, 1 above/equal `strong`, and linearly interpolate.
5. Admit a semantic candidate at calibrated score >= 0.5, or a lexical candidate with
   raw score >= 12. When semantics are available, lexical admission also requires a
   positive calibrated semantic score in semantic top-K; this rejects misleading overlap.
   That conservative gate is deliberately bypassed for profiles and backend failure.
6. Apply fusion, minimum score (default 0.5), then `UsageBudget`'s strict score threshold,
   serialized-reference char limit and item limit. Oversized rows are skipped. Zero is
   normal; slots are not filled with arbitrary `score > 0` candidates.

For lexical score normalization, the provisional scale is 24 (clipped at 1). The original
raw lexical order is preserved in lexical-only/profile and the lexical-first priority
partition, including where normalization clips multiple scores. Stable ties use input
order, never source priority. Runtime ownership/world_fact does not outrank static canon.
Existing interpretation/unknown guards and provenance stay attached to selected rows.

| Fusion | Experimental rule | Tradeoff |
| --- | --- | --- |
| `lexical_first` (default) | qualifying lexical partition in original order, then semantic recall | Smallest policy; preserves lexical order but can retain semantically borderline overlap |
| `weighted` | `(1-w)*clipped_lexical + w*calibrated_semantic`, default w=0.5 | Scale/weight sensitive; needs measured tuning |
| `rrf` | sum of `(k+1)/(k+rank)` over available channels, default k=60 | Rank-scale independent; absolute admission gates remain essential |

RRF scores can approach 2; other scores are <=1. Budget thresholds are backend/fusion
scale, not probabilities. The common 0.5 trial cutoff is not claimed to be optimal across
methods. Lexical-first partition priority can produce a non-monotonic score sequence.
No top1/top2 separation is imposed before measurements establish a benefit. The default
is a conservative **implementation baseline, not an empirical winner**.

A missing backend/calibration, backend-key mismatch, HTTP failure, timeout or invalid
vector uses lexical-only fallback with the same entity gate and nonzero lexical cutoff.
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
This is an explicitly invoked, billable API evaluation. With unchanged 130-row corpus
and 12 cases, the default run embeds 130 unique candidate texts plus 12 queries; enabling
intent comparison adds 12 query embeddings, not another corpus fill. Counts can differ
if text is duplicated. Every new process starts with an empty cache. No production logs,
private conversations or runtime DB are read by the CLI.

A custom reviewed lore JSONL can be supplied with `--lore`, and fixtures with `--cases`.
`--reject` and `--strong` must be supplied together to compare explicit trial thresholds.
`--top-n` changes selection size; default 3 with a 3,200-character budget. The direct
Python API also accepts a prepared static/runtime candidate union. The production
retriever intentionally skips conversation/profile semantic calls; the calibration CLI
measures **all fixture queries** to expose negative distributions, then reproduces
those selection skips when reporting fusion results.

Fixture schema: unique `id`, `split` (`calibration` or `evaluation`), `visible_text`,
explicit `intent`, and disjoint `positive`, `hard_negative`, `unrelated` candidate-id
lists. Optional `retrieval_text`, `anchor_text`, `entities`, `required_entities` describe
already-authorized routing inputs. Labels refer to packaged, reviewed lore; no corpus
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

Output is JSON (stdout unless `--output`), with:

- backend key, dimensions, corpus size, corpus/fixture SHA-256, experimental status;
- per query representation: trial thresholds and all fusion settings;
- calibration/evaluation distributions per label: count/min/median/mean/max;
- per case: labeled cosine values, semantic top ids/cosines, timing/cache counts,
  and each fusion's selected ids/scores, positive/hard-negative/unrelated hits,
  unjudged selections and zero-result status;
- split-specific recall@N, hard-negative rejection, labeled profile precision,
  zero-result accuracy and unjudged counts for lexical-only and the three fusion methods.

Reports contain fixture/candidate ids and scores, not query/corpus text or credentials.
Unjudged candidates are not automatically labeled wrong or correct; inspect them before
interpreting precision. Scores above are genuinely measured **only when run with the
real API**. Unit tests use synthetic vectors solely to verify the mechanics. This PR
contains no live distributions, measured latency, or evidence that one fusion wins.

## Next boundaries and review

Review semantic admission vs lexical preservation, fixture labels/splits, candidate cache
lifecycle and Gemini request format especially carefully. For this corpus, packaged
birthday/weapon lookups have lexical evidence; some school-year/affiliation/position
queries have no good current lexical hit. #312 handles that grounding gap independently.

No changes to `knowledge_retrieval.py`, `retrieval_v2.py`, `retrieval_request.py`,
`lore.py`, `runtime_knowledge.py`, packaged lore or shared contract documentation: #312
should have no direct edited-file conflict. #314 owns ambient activation; #315 owns
structured evidence sufficiency/web fallback; #316 owns live result review, production
shadow comparison, telemetry and rollout gates. Before wiring production, explicitly
review calibration for the chosen model/revision/dimensions/query representation and
relevance policy. There is deliberately no production enable switch in this PR.
