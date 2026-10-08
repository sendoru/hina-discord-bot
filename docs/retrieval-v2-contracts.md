# Retrieval v2 contracts (#311)

This milestone is an **opt-in contract and lexical adapter**, not a production rollout.
`LLM.lore_references()`, runtime-first packing, `lore_reference`, and `enough_local()`
continue to use the legacy path. Entity grounding, hybrid ranking, ambient activation,
structured sufficiency, and shadow comparison follow in #312–#316.

## Inputs

`RetrievalRequest` keeps literal `visible_text`, normalized/expanded `retrieval_text`,
and authorized causal `anchor_text` / `anchor_source` separate. The AI-layer
`build_retrieval_request(RoutingPlan, ...)` reuses the existing deterministic information
classifier and prefix/query normalization without a new LLM call. It does not read
memory, sample channel history, or change egress policy. Inputs contain user content;
the request must not be serialized into telemetry.

`entities` carries resolved canonical entity ids, never aliases or subject strings.
`required_entities` specifies the evidence constraint, including a pair when needed.
Both are empty until supplied by an entity resolver; this milestone does not guess them.
The request's intent describes the query; candidate usage describes eligible purposes.
They are independent so a conversation can later retrieve ambient insight.

The existing classifier combines relationship/event queries. The bridge preserves that
as `relationship_or_event` instead of inventing a distinction. The contract also permits
explicit `relationship` and `event` intents when later routing can establish them.
Redundant factual/relation booleans and speculative scene features are not added.

## Shared candidates and provenance

The existing frozen `KnowledgeCandidate` is extended additively instead of duplicating
static/runtime models. `RankedKnowledgeCandidate` keeps backend ranking score and stable
input order, separate from confidence. A score is backend-scale relevance, **not raw
embedding cosine** or evidence certainty. Future semantic scoring must calibrate first;
this milestone's adapter has only existing integer lexical scores.

| Field | Meaning |
| --- | --- |
| `candidate_id` | Stable knowledge row id, not canonical entity id |
| `source` | Storage provenance: `static_lore` / `runtime_knowledge` |
| `lane` | Existing corpus classification: `canon` / `community_meme`; unavailable for runtime |
| `usages` / `retrieval_usages` | Retrieval purpose(s): factual, relation, ambient, reaction |
| `entities` | Explicit canonical entity ids, separate from lexical subjects |
| `fact_type`, `awareness`, `confidence` | Evidence class, existing knowledge level, review confidence |
| `time`, `kr_release` | Existing timeline and release provenance |
| `source_metadata` | Original source type/title/locator/url, separate from storage source |
| `semantic_text` | Optional reviewed meaning text; fallback is summary/content `search_text` |
| `evidence_ids` | Linked fact ids, not automatically expanded or validated for existence |

Neither storage source nor corpus lane is a ranking priority in the adapter. Runtime
rows have no review-confidence/source-lane fields: absent values stay `None` / empty.
Admin ownership and `world_fact` kind are not synthesized into verified fact evidence.
Runtime interpretation keeps its existing certainty guard and stays explicit lookup.

Static JSONL accepts optional `usage` (one value or a non-empty unique list), `entities`,
`semantic_text`, and `evidence_ids`. Old rows need no rewrite or database migration.
Multiple usages allow a reviewed inference to support both explicit QA and ambient RP
without duplicating its identity. Defaults are factual for canon (including inference
and unknown guards) and reaction for community memes. Ambient eligibility requires
`fact_type=inference`; community memes are reaction-only. Eligibility is not activation.

The lore ingestion queue already uses `evidence` for a **text excerpt**, and strips it
when publishing reviewed rows. Linked facts therefore use `evidence_ids` to avoid
reinterpreting that existing field. No corpus reclassification or editorial changes are
included here. New metadata is preserved by `LoreIndex.candidates()` but is not added to
the legacy reference serializer or used by legacy ranking. CLI authoring UI is deferred.

## Bundle and lexical adapter

`KnowledgeBundle` holds ranked candidates in four independent sections:

| Section | Eligible usage | Consumption semantics |
| --- | --- | --- |
| `facts` | factual | Explicit lookup references; retain interpretation and unknown guards |
| `relations` | relation | Grounding evidence; entity/quality policy still required in #312/#315 |
| `character_insights` | ambient | Optional scene-relevant reaction context; not an official fact list |
| `reactions` | reaction | Optional behavior guidance; not factual evidence |

Insight need not be spoken aloud, remains interpretation, and yields to direct current
conversation evidence. Adding an insight or reaction must never satisfy factual/relation
web fallback. Bundle membership alone is not a local-sufficiency verdict.

`lexical_bundle(request, candidates, budgets=...)` ranks once with the existing scorer,
then selects per usage. Each explicitly enabled retrieval usage has its own `UsageBudget`
(items, serialized reference characters, strict score threshold). Omitted or non-positive
budgets disable a section. Oversized rows are skipped; weak or absent matches leave
slots empty. A multi-usage candidate may appear in both sections. Stable ties preserve
input order. Thresholds are in the ranker's scale; parity uses the legacy `score > 0`.
No final production thresholds or weights are implied by this baseline.

`context_sections()` is an explicit model-serialization boundary, reusing legacy item
semantics while retaining all four sections. It does not flatten them or serialize
request content, ranking diagnostics, or source metadata. Typed candidates retain rich
provenance for #315/#316; content-free telemetry must be designed separately in #316.
Consumers must add retrieval usage semantics to model context when a usage is activated (#314).

The adapter accepts candidates from `LoreIndex`, `RuntimeKnowledgeRegistry`, or their
union. It performs no entity matching, grounding, semantic retrieval, or scene filtering;
callers must apply those policies before enabling future retrieval usage budgets. The adapter
is deliberately not invoked from the production answer path yet.

## Follow-on work

- #312: canonical resolver, exact profile/entity-pair grounding and ambiguity guards.
- #313: lexical/semantic union, calibration, fusion eval and lexical fallback. No
  provider, vector database or external infrastructure is part of these contracts.
- #314: reviewed ambient eligibility, small scene budget, activation and model semantics.
- #315: evidence sufficiency from metadata, intent and required entity matches.
- #316: production shadow wiring, content-free diagnostics and reversible rollout.

Tests cover single-usage lexical parity (including packaged corpus), independent retrieval
usage budgets, zero results, thresholds, stable ties, source neutrality, additive validation,
runtime compatibility, interpretation/unknown guards, reaction serialization, and causal
request wiring. They do not pin model prompt prose or claim live-answer quality gains.
