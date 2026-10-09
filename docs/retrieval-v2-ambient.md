# Scene-aware ambient character insights (#314)

This is an **opt-in Retrieval v2 API**. Production still uses the legacy retrieval/context
path until #316. Ambient retrieval never triggers web search and has no lexical fallback.

## Purpose

Ambient insights are reviewed interpretations that may shape the current roleplay reaction.
They are not an answer fact list and do not need to be spoken aloud.

The model-facing consumption rule is represented by `AMBIENT_CONTEXT_POLICY`:

- use an insight only when it fits the current scene;
- reflect it naturally through tone, hesitation, priorities or behavior;
- do not routinely explain the interpretation as self-analysis;
- never promote an interpretation to confirmed official fact;
- direct evidence in the current conversation overrides an ambient interpretation.

## Scene query boundary

`AmbientSceneContext` supplies only bounded, caller-authorized scene signals:

- canonical current RP entity — **filter only**, never embedded as text;
- current visible message from `RetrievalRequest`;
- causal/explicit-reply anchor from `RetrievalRequest`;
- newest at most two already-authorized same-speaker turns;
- one small natural-language relationship projection signal.

The query deliberately excludes:

- lexical-expanded `retrieval_text`;
- canonical ids as pseudo-natural-language embedding text;
- full channel history;
- full structured/long-term memory;
- unrelated channel ambient context;
- system prompt / character prompt / world core.

Current/anchor/recent/relationship parts are whitespace-normalized, bounded, and exact
meaning duplicates are removed.

## Eligibility

A candidate is considered only when all of these hold:

- `usage=ambient`;
- `fact_type=inference`;
- the candidate's canonical `entities` contains the current RP entity.

This is a hard entity filter, not a semantic boost. Source/static/runtime provenance is not
a ranking tier. The current packaged ambient rows are reviewed canon interpretations for
`character.hina`.

## Ranking

Ambient retrieval is intentionally simpler than factual retrieval:

```text
eligible ambient interpretations
        ↓
one bounded scene query embedding
        ↓
cosine against every eligible cached candidate
        ↓
SemanticCalibration
        ↓
high semantic admission threshold
        ↓
ranked ambient rows
        ↓
BundleComposer
max 1~2 items / small char budget
```

There is no lexical/semantic fusion, RRF, entity nomination union, or top-K candidate
generation. The corpus is tiny, so every eligible ambient row is scored locally after one
query embedding.

`AmbientConfig.semantic_min=0.75` is a **provisional precision-first setting**, not a live
calibrated production threshold. `calibration=None` means no semantic call. Backend or
calibration mismatch/failure returns zero ambient rows; it does not fall back to weak
lexical matches and never blocks the answer.

Repeated retrieval of the same insight is allowed while the scene remains relevant.
Preventing the model from repeating an explicit self-analysis sentence is a consumption
behavior problem, not a retrieval suppression rule.

## Composition

`AmbientRetriever` returns admitted `RankedKnowledgeCandidate` rows only.
It does not create or mutate `KnowledgeBundle`.

The shared `BundleComposer` owns packing and cross-section deduplication:

```python
ambient = await ambient_retriever.retrieve(request, scene, candidates)

bundle = BundleComposer({
    KnowledgeUsage.AMBIENT: UsageBudget(2, 900),
}).compose({
    KnowledgeUsage.AMBIENT: ambient.rows,
})
```

Because composer ownership is relation → factual → ambient → reaction, an interpretation
already selected as explicit factual evidence is not serialized a second time as ambient
context.

## Initial reviewed Hina ambient rows

Five existing inference rows are additionally marked `usage=[factual, ambient]`, retain
their original factual lookup behavior, gain `entities=["character.hina"]`, and link
supporting reviewed rows through `evidence_ids`:

- `canon.hina.rest_discomfort_pattern`
- `canon.hina.unfamiliar-with-ordinary-leisure.inference`
- `canon.hina.relaxes-around-teacher.inference`
- `canon.hina.relationship.inference.cautious_desire_spend_time`
- `canon.hina.dress.inference.private-time-importance`

These cover responsibility/rest tension, unfamiliar ordinary leisure, relaxing around the
teacher, cautious desire for private time, and the importance of uninterrupted private
time. More interpretations should be added only after review; ambient eligibility is not a
license to reclassify every inference.

## Boundaries

- #315 decides proposition-level factual/relation evidence sufficiency. Ambient rows never
  make local factual evidence sufficient and never trigger web fallback.
- #316 owns production invocation, extraction of authorized same-speaker/relationship
  signals, content-free telemetry, live threshold evaluation, prompt integration, and
  reversible rollout.
- No live Gemini distribution is claimed in this PR. Unit tests use deterministic fake
  geometry to verify filtering, zero-result behavior, full eligible-corpus scoring,
  cache reuse and composition.
