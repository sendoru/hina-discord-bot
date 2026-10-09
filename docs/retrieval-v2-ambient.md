# Retrieval v2 ambient character insight lane

Issue: #314. Part of #310.

This lane supplies a **small, scene-relevant interpretation context** for roleplay. It is
not a factual-answer reference source and it never triggers web search.

## Data contract

Ambient rows remain ordinary reviewed canon lore rows. No new storage schema is added.

A row eligible for ambient retrieval must have:

- `fact_type=inference`
- `knowledge=inference`
- `usage=["factual", "ambient"]` when it should remain usable for explicit psychology QA
- canonical `entities`
- non-empty `evidence_ids`

The first reviewed Hina ambient set contains five interpretations:

- difficulty putting responsibility down even when she wants rest
- unfamiliarity with ordinary leisure
- relaxing when the teacher is present during severe workload
- cautiously expressing a desire for private time
- valuing praise/attention in private time

These rows remain interpretations. Their evidence ids are supporting reviewed facts, not a
claim that the interpretation is a direct quote or immutable trait.

Audience-only interpretation rows are deliberately excluded from ambient context.

## Scene query

`ambient_query()` is separate from factual lexical/semantic query construction. It can use
only bounded scene inputs supplied by the caller:

- current visible message
- authorized causal/reply anchor
- at most two recent same-speaker turns
- aggregate relationship-evidence axes only

It does **not** include:

- lexical keyword expansion / `retrieval_text`
- canonical entity ids as embedding prose
- raw structured memory items
- full relationship observations
- full conversation/channel history
- system/character/world prompts

Recent text segments are normalized and bounded; duplicate current/anchor/recent text is
removed. Relationship context serializes only the small 0..4 aggregate axes such as comfort
or support openness, never the raw memory that produced them.

## Candidate filtering

Ambient retrieval is precision-first. A candidate must be:

- `usage=ambient`
- canon
- an `inference`
- in-character `knowledge=inference`
- explicitly attached to the current RP entity

The current implementation defaults the RP entity to `character.hina`. Relation-pair
metadata is irrelevant to this lane.

## Ranking

Ambient ranking is semantic-only. There is no lexical fallback or web fallback.

A caller must explicitly provide a backend-matched `SemanticCalibration`. Without it,
ambient retrieval returns zero rows and performs no embedding calls.

When enabled:

1. build the bounded scene query;
2. score every eligible ambient row in the small local corpus;
3. map cosine through `SemanticCalibration`;
4. admit only rows at or above the ambient `min_score` (experimental default 0.75);
5. return ranked rows in semantic order;
6. let `BundleComposer` apply the small ambient item/character budget.

Recommended initial packing for evaluation is:

```python
UsageBudget(max_items=2, max_chars=900)
```

The threshold is not a production approval. Live Gemini calibration is still required
before #316 can enable the lane.

Backend failure, timeout, calibration mismatch, missing calibration, or unrelated scenes all
degrade to **no insight**. The lane does not fall back to weak lexical psychology matches.

## Repetition semantics

Retrieval does not maintain "already used this insight" state. If the same insight remains
highly relevant across a continuing scene, retrieving it again is valid.

The model/prompt layer should avoid repetitively *saying* the same self-analysis. Retrieval
repetition and answer-expression repetition are separate concerns.

## Model semantics

When #316 wires `KnowledgeBundle.character_insights` into production context, the
instructional meaning must remain:

- use the insight only to shape a natural response when relevant;
- do not feel obligated to mention or explain it;
- do not turn an interpretation into an official/direct fact;
- current conversation evidence overrides a generic interpretation;
- do not narrate internal psychology merely because an insight was retrieved.

This PR does not wire the section into the live answer request.

## Evaluation

`evals/retrieval_v2_ambient.jsonl` contains calibration/evaluation scenes for:

- being told to stop working and rest
- ordinary shopping/leisure
- praise after effort
- supportive presence after overwork
- private time
- unrelated lunch/factual queries

Validate without an API key:

```bash
python -m hina_bot.tooling.ambient_calibration --validate-only
```

With `GEMINI_API_KEY` in the environment, run a live report:

```bash
python -m hina_bot.tooling.ambient_calibration \
  --output data/logs/retrieval-v2-ambient-calibration.json
```

The report includes candidate/query embedding token usage, request count, latency, cosine
distributions, trial thresholds, selected rows and zero-result behavior. Current price can
optionally be supplied with `--usd-per-million-tokens`; no price is hard-coded.

The live report is experimental and does not mutate runtime settings.

## Boundaries

- #315: ambient rows never affect local factual-evidence/web-fallback sufficiency.
- #316: owns production invocation, timeout/concurrency/shadow telemetry, live calibration
  approval, prompt/context wiring and rollout.
- `BundleComposer`: owns final character-insight packing/deduplication.
- `SemanticIndex` / Gemini adapter: reused unchanged.
