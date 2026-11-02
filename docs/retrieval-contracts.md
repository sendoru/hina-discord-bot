# Retrieval contracts (#311)

> **Status (2026-11-02, #463):** the retrieval v2 runtime was removed: the `shadow` and
> `v2` RAG modes, hybrid/semantic and ambient retrieval, the exact relationship grounder,
> and the `UsageBudget`/`pack_ranked`/`BundleComposer`/`lexical_bundle` packing helpers.
> What remains are the `RetrievalRequest`, `RetrievalIntent` and `KnowledgeBundle` types in
> `core/retrieval_contracts.py` (formerly `core/retrieval_v2.py`) and the candidate
> metadata below. The pre-search local-evidence check uses them; see
> [Structured local evidence sufficiency](evidence-sufficiency.md). Answers use
> `RAG_MODE=full` (default) or `v1`; see [full-lore-mode.md](full-lore-mode.md). The rest of
> this page is the design record from the time the contracts were introduced.

This milestone began as an **opt-in contract and lexical adapter**, not a production
rollout. `LLM.lore_references()`, runtime-first packing and `lore_reference` still use
the legacy answer-context path until #316. #312–#315 now provide entity grounding, hybrid
ranking, ambient activation and structured evidence sufficiency behind those contracts;
#315 also replaces the legacy relation-wording web-fallback heuristic while preserving the
legacy selected references. #316 owns the final v2 retrieval rollout.

## Inputs

`RetrievalRequest` keeps literal `visible_text`, normalized/expanded `retrieval_text`,
and authorized causal `anchor_text` / `anchor_source` separate. The AI-layer
`build_retrieval_request(RoutingPlan, ...)` reuses the existing deterministic information
classifier and prefix/query normalization without a new LLM call. It does not read
memory, sample channel history, or change egress policy. Inputs contain user content;
the request must not be serialized into telemetry.

`entities` carries resolved canonical entity ids, never aliases or subject strings.
`relation_pair` is an optional complete, unambiguous two-entity constraint owned by the
exact relationship lane. Factual/ambient retrieval must not reinterpret it as a generic
candidate filter. It is `None` until supplied by the entity bridge.
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
| `evidence_ids` | Linked fact ids used to support reviewed derived claims |
| `claims` | Optional typed propositions for sufficiency: predicate, canonical direction, value, time scope and polarity |

Neither storage source nor corpus lane is a ranking priority in the adapter. Runtime
rows have no review-confidence/source-lane fields: absent values stay `None` / empty.
Admin ownership and `world_fact` kind are not synthesized into verified fact evidence.
Runtime interpretation keeps its existing certainty guard and stays explicit lookup.

Static JSONL accepts optional `usage` (one value or a non-empty unique list), `entities`
and `evidence_ids`. Old rows need no rewrite or database migration. The `semantic_text`
field was only read by the removed semantic retrieval and is now ignored (#463).
Multiple usages allow a reviewed inference to support both explicit QA and ambient RP
without duplicating its identity. Defaults are factual for canon (including inference
and unknown guards) and reaction for community memes. Ambient eligibility requires
`fact_type=inference`; community memes are reaction-only. Eligibility is not activation.

The lore ingestion queue already uses `evidence` for a **text excerpt**, and strips it
when publishing reviewed rows. Linked facts therefore use `evidence_ids` to avoid
reinterpreting that existing field. #315 adds optional `claims` for proposition-level
sufficiency; claims are typed metadata and are never inferred from summary wording.
`LoreIndex.candidates()` preserves them while the model-facing legacy reference serializer
does not expose them. CLI authoring UI remains deferred.

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

The packing helpers (`UsageBudget`, `pack_ranked`, `BundleComposer`, `lexical_bundle`)
and `context_sections()` were removed with the v2 runtime (#463). The evidence check
builds a `KnowledgeBundle` directly from the selected references.

## Follow-on work

- #312: canonical resolver, exact profile/entity-pair grounding and ambiguity guards.
- #313: lexical/semantic union, calibration, fusion eval and lexical fallback. No
  provider, vector database or external infrastructure is part of these contracts.
- #314: implemented opt-in scene-aware semantic ambient retrieval (removed in #463).
- #315: implemented structured proposition-level evidence sufficiency and web-fallback routing; see [Structured local evidence sufficiency](evidence-sufficiency.md).
- #316: production shadow wiring, content-free diagnostics and reversible rollout (removed
  in #463; production uses `RAG_MODE=full`).

Remaining tests cover additive metadata validation, runtime compatibility,
interpretation/unknown guards, reaction serialization, bundle usage eligibility and causal
request wiring. They do not pin model prompt prose or claim live-answer quality gains.

The resolver from #312 (still used by the evidence check) is documented in
[Canonical entity and relationship grounding](entity-grounding.md).
