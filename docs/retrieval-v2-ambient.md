# Retrieval v2 ambient character insight

Status: opt-in implementation for #314. Production answer assembly remains legacy until #316.

## Purpose

Ambient retrieval is not factual QA. It selects a very small number of reviewed character
interpretations that can shape Hina's reaction in the current scene.

The model-facing semantics are:

- the insight does not need to be quoted, explained or self-diagnosed;
- use it only when it naturally fits the current scene;
- it remains an interpretation, not an official fact;
- direct evidence in the current conversation overrides a general insight;
- an empty result is preferred to a weak psychological guess.

The selected rows land in `KnowledgeBundle.character_insights`, not `facts`.

## Reviewed data

Only five existing Hina interpretations are initially marked with
`usage=["factual", "ambient"]`:

- difficulty putting responsibility/work down even when rest is wanted;
- unfamiliarity with ordinary student leisure;
- relaxing/feeling safer around the teacher under heavy workload;
- cautiously expressing a wish for private time because of work/burden/public attention;
- valuing recognition/attention for personally meaningful effort.

Each ambient row has:

- `fact_type=inference`;
- `entities=["character.hina"]`;
- a natural `semantic_text` written for scene matching, not lexical keyword matching;
- `evidence_ids` pointing to reviewed factual/reported rows.

Keeping `factual` usage preserves explicit psychology QA. `BundleComposer` deduplicates the
same row if factual and ambient retrieval both select it.

## Scene representation

`AmbientScene` accepts only a small authorized projection:

- current `RetrievalRequest.visible_text`;
- the already authorized causal `anchor_text`;
- at most two explicitly supplied recent turns, each at most 600 characters;
- one short natural-language relationship signal;
- current RP entity as a **filter**, not embedding text.

`ambient_scene_query()` never consumes `retrieval_text`, canonical ids, system prompts,
`hina.md`, `world_core`, structured memory, or channel history. The caller must not put
those sources into `recent_turns` or `relationship_signal`.

The causal anchor is reused through the same natural semantic representation as factual
semantic retrieval. Duplicate/contained scene fragments are collapsed.

## Candidate eligibility

Ambient retrieval is intentionally stricter than factual retrieval. A row must be:

- `usage=ambient`;
- canon lane;
- `fact_type=inference`;
- explicitly tagged with the current RP entity;
- linked to at least one `evidence_id`.

There is no runtime/static priority and no relation-pair coupling.

## Ranking and failure behavior

The lane is semantic-only:

1. filter the reviewed ambient corpus for the RP entity;
2. embed the small natural scene query once;
3. compare it with every eligible ambient candidate;
4. calibrate cosine with an explicit backend-matched `SemanticCalibration`;
5. admit only rows at or above the precision-first `semantic_min` (default calibrated 0.75);
6. return ranked rows to `BundleComposer`.

There is deliberately:

- no lexical fallback;
- no web fallback;
- no fixed top-K candidate-generation gate;
- no weighted/RRF policy;
- no mechanical repeat-suppression state.

If the backend/calibration is missing, mismatched or fails, the result is empty. Ambient
context is optional and must never make an answer fail.

The default configuration makes **no embedding call without an explicit calibration**.
The numeric `semantic_min` is on the calibrated 0..1 relevance scale, not raw cosine.
Live Gemini measurement is still required before #316 can enable this in production.

## Packing and repetition

The retriever does not own item/character limits. A caller can use the shared composer:

```python
bundle = BundleComposer({
    KnowledgeUsage.AMBIENT: UsageBudget(max_items=2, max_chars=1200),
}).compose({
    KnowledgeUsage.AMBIENT: ambient_result.rows,
})
```

One or two items is the intended upper bound; zero is normal.

The same insight may be retrieved on consecutive turns when the scene remains relevant.
That is different from making the model repeat the same explicit self-analysis. Response
style/prompt integration belongs to #316; retrieval does not invent temporal suppression
state that could hide a still-relevant insight.

## Boundary with later issues

- #315 decides factual/relation evidence sufficiency and web fallback. Ambient absence never
  triggers web search.
- #316 owns production invocation, timeout/concurrency, live calibration, telemetry and
  final context wording.
- `EntityResolver`, `SemanticIndex`, Gemini batching/cache behavior and factual retrieval
  policy are reused unchanged.
