# Structured local evidence sufficiency (#315)

Part of #310. This layer answers a different question from retrieval:

> Do the selected local rows actually support the proposition the user asked?

Retrievers still find relevant candidates. Evidence sufficiency checks proposition coverage
before local knowledge is allowed to suppress web fallback.

## Why

The legacy rule searched selected reference **wording** for terms such as meeting,
conversation, confrontation or incident. A summary edit could therefore change web
routing even when the underlying evidence was identical, and a row about the same two
characters could incorrectly satisfy a different question.

The v2 path never scans candidate content for relation keywords.

## Typed claims

Reviewed lore may carry optional `claims`. A claim records:

- `predicate`: the proposition family;
- `subject` / optional `object`: canonical character ids;
- optional `value`;
- optional normalized `time_scope`;
- `polarity`: positive, negative or unknown.

Current predicates are deliberately small:

- `school_year`
- `school_year_relation`
- `affiliation`
- `role`
- `direct_meeting`
- `awareness`
- `relationship_state`
- `addressing`

Claims are editorial metadata. They are not generated from summaries at runtime and are
not serialized into the model-facing reference item.

Examples:

```json
{
  "predicate": "awareness",
  "subject": "character.hina",
  "object": "character.hoshino",
  "value": "past_known",
  "time_scope": "before_first_meeting",
  "polarity": "positive"
}
```

```json
{
  "predicate": "addressing",
  "subject": "character.hoshino",
  "object": "character.hina",
  "value": "given_name",
  "time_scope": "post_set_battle",
  "polarity": "positive"
}
```

## Query-side requirement

`evidence_requirement()` extracts only high-confidence proposition families from the
question itself. It does not inspect candidate summaries.

The initial deterministic coverage handles:

- awareness before first meeting;
- direct / first meeting;
- school-year relation such as senior/junior/same-year questions;
- addressing;
- generic relationship state.

Direction is preserved for directional predicates such as awareness and addressing.
Meeting, relationship state and school-year relation are symmetric.

Time qualifiers are also part of matching. For example:

- a first-meeting claim does not answer whether Hina already knew Hoshino **before** meeting;
- a historical post-event addressing claim does not by itself answer what someone calls
  another person **currently**.

If a relation/event proposition cannot be classified safely, sufficiency fails closed
instead of treating the same entity pair as enough evidence.

## Evidence quality

A local row can suppress web fallback only when it is reviewed canon evidence:

- reviewed confidence: verified / official_secondary / crosschecked;
- confirmed Korean release;
- source provenance is present.

Direct fact, visual fact and reported fact are direct evidence classes.

An explicit `fact_type=unknown` claim can also be sufficient. This means the local
knowledge can answer "there is no confirmed basis" without being promoted to a positive
fact.

An inference is not sufficient merely because it was retrieved. A derived claim can be
sufficient only when:

- it has `evidence_ids`; and
- every required supporting row resolves to reviewed direct/derived evidence.

This is used for the Hina/Hoshino same-school-year relation, which is derived from two
reviewed profile rows.

Unreviewed runtime knowledge has no reviewed confidence/source/release metadata and
therefore does not become verified evidence merely because an administrator created a
`world_fact` row.

## Bundle semantics

Only:

- `KnowledgeBundle.facts`
- `KnowledgeBundle.relations`

participate in factual sufficiency.

`character_insights` and `reactions` never satisfy factual/relation web fallback.
Ambient psychology therefore cannot stop factual verification.

## Production bridge before #316

The answer path still selects legacy references until #316. #315 removes the old
wording heuristic without prematurely enabling the v2 retrievers:

1. legacy local references are selected exactly as before;
2. their stable reference ids are mapped back to typed `KnowledgeCandidate` rows;
3. those already-selected candidates are adapted to a small evidence bundle;
4. `assess_local_evidence()` decides proposition coverage;
5. `LOCAL_THEN_WEB` suppresses web only when that assessment is sufficient.

This keeps answer-context retrieval behavior unchanged while replacing the brittle web
fallback decision.

#316 will supply the same assessor with the actual v2 composed bundle during shadow and
rollout.

## Search decision

For `LOCAL_THEN_WEB`:

```text
structured assessment missing/insufficient
        -> required web

structured assessment sufficient
        -> local only
```

Other deterministic routing rules remain unchanged.

The assessment result is content-free and contains only reason, matched candidate ids,
predicate and answer state, so #316 can expose appropriate telemetry without logging
query or reference text.

## Current corpus annotations

Initial proposition claims cover:

- Hina and Hoshino profile school years;
- their derived same-school-year relation;
- Hina knowing Hoshino's past before their first direct meeting;
- their first direct meeting;
- explicit uncertainty about Hina's exact knowledge of Hoshino's trauma at Eden Treaty;
- their later closer relationship;
- Hoshino addressing Hina by given name after the Set battle.

This is intentionally not an attempt to tag all 130 lore rows. Generic factual queries can
still use reviewed direct selected facts; claims are required where relation/event
answerability would otherwise depend on entity overlap or summary wording.

## Boundaries

- Retrieval relevance/admission remains #312/#313/#314.
- #315 decides answerability and web fallback only.
- #316 owns v2 production invocation, shadow comparison, content-free telemetry and
  rollout/rollback.
- No LLM proposition-rewrite call is added.
- No web search is triggered by ambient-insight absence.
