# Canonical entities and exact relationship grounding (#312)

This is an **opt-in API**, based on #311/#318. Production still calls the legacy
`LLM.lore_references()` and `enough_local()`. No extra LLM calls, runtime settings,
SQLite migration, telemetry, prompt injection, embedding, or hybrid scoring are added.
The existing Hina prompt's evidence-based addressing/name fallback is unchanged.
Live answer quality is not measured by the structural regression tests in this PR.

## Entity boundary

`core/entity_resolution.py` owns a small explicit registry of `CanonicalEntity`
(id, display name, aliases). The initial corpus contains only `character.hina` and
`character.hoshino`. A caller can supply additional reviewed entities; tests exercise
an additional character and colliding aliases. Titles such as committee president,
old man, and senior are deliberately not aliases. This is world-character resolution,
not Discord member identity resolution or arbitrary real-world named-entity recognition.

Normalization is NFKC + casefold + collapsed whitespace. Whole aliases take priority;
mention matching uses longest alias first, Unicode word boundaries, and a closed list
of standalone Korean/Japanese particles. It does not remove arbitrary name suffixes,
transliterate unknown names, or perform fuzzy matching. A colliding alias remains
ambiguous, including when a shorter substring would have resolved. The full name can
still resolve when only its short alias collides. Combining marks and identifier prefixes
cannot turn a partial token into a match. Japanese names/full names are supported;
unsegmented Japanese sentences and unsupported compound particles can remain unresolved.
The registry is scoped to the Blue Archive RP domain, not universal name disambiguation.

`resolve_alias()` returns a canonical id or `None`. `resolve()` returns deterministic,
first-mention ordered ids and ambiguous aliases. Neither raw text nor ambiguous aliases
are telemetry. Lexical `subjects` remain separate and never become canonical metadata.

## Request bridge

The original `build_retrieval_request()` retains its behavior and caller-supplied ids.
`build_resolved_retrieval_request()` explicitly opts in:

- It resolves visible text before using the existing classifier's normalized lore query.
- The RP subject is configured as `character.hina`, can be another registered id, or
  can be disabled with `None`. User first-person pronouns do not change that identity.
- A single named character grounds the RP subject/counterpart pair. Two explicit
  characters constrain that pair; the RP subject is not added as a third required id.
- A self-profile request can require just the RP subject. Ordinary text without a
  resolved character has no required entity constraint and retrieves no relations.
- An authorized anchor with provenance is inherited only for the existing deterministic
  follow-up classification and a narrow set of name-free elliptical questions
  (such as “그럼 무슨 사이야?”) when no character is explicit in the current message.
  Unknown/new names and unsupported follow-up forms cannot inherit an old identity. Ambiguity blocks automatic constraints; explicit targets override the anchor.
- More than two explicit characters do not synthesize a pair. Ambiguous aliases stay
  unresolved; registered ids can still be present in `entities` without a constraint.

The bridge adds no channel-history/memory access. Expanded lexical hints and classifier
context are not identity evidence. Automatic constraints describe RP grounding targets,
not a claim that every referent in a sentence was understood. Unregistered characters
remain unresolved; callers with a more precise required pair can use the original builder.

## Exact grounding and integration

`RelationshipGrounder` indexes supplied `KnowledgeCandidate` rows by their explicit
canonical entity set. It accepts reviewed canon rows with relation eligibility,
confirmed KR release, source metadata, and usable evidence types. Inferences require
linked evidence ids and remain interpretations. Reviewed unknown rows retain their
existing guard. Missing data creates neither a fact nor an absence-of-relationship guard.
Runtime rows currently lack reviewed canonical metadata, so admin ownership or lexical
subjects do not promote them to relation evidence. This restriction is provenance-based;
there is no static/runtime ranking tier.

Selection requires a nonempty `required_entities` subset of `entities`. Single-entity
profile/background rows may support that constraint; multi-entity rows must match the
**complete set**, not merely overlap it. An unrelated Hina/third-character relation cannot
satisfy Hina/Hoshino. Profiles alone do not establish a direct relationship. Requested
profiles precede pair rows; corpus order is stable within each group. Duplicate ids are
selected once. `UsageBudget` independently bounds relation item count and serialized
reference characters; oversized rows are skipped. A score of 1 means binary exact
eligibility, not certainty, semantic similarity, or a score to fuse with #313.

```python
from hina_bot.ai.retrieval_request import build_resolved_retrieval_request
from hina_bot.core.relationship_grounding import RelationshipGrounder
from hina_bot.core.retrieval_v2 import UsageBudget

request = build_resolved_retrieval_request(routing_plan)
# Construct/cache the immutable candidate index outside the per-turn hot path.
grounder = RelationshipGrounder.load()
bundle = grounder.bundle(request, budget=UsageBudget(6, 3200), base=factual_bundle)
sections = bundle.context_sections()
```

`base` is optional. The composer **replaces** its relations with exact results while
preserving facts, insights, and reactions. It does not call or alter `lexical_bundle`.
A caller enabling the exact path must use this composer/`ground()` instead of selecting
relations with a lexical budget. Relation rows are supplied from the full corpus, never
from factual top-N results. `RELATION_CONTEXT_POLICY` is the generic consumption policy
for a future v2 assembler: keep school/year distinct from age/rank/respect, do not infer
cross-school seniority or addressing, respect directional/timeline evidence, and use a
name when no confirmed addressing is available. It is not injected into production yet.

## Minimal corpus changes and Hoshino regression

Three existing `data/lore.jsonl` rows gain only canonical `entities` and
`usage=[factual, relation]`; text, source, timeline, and legacy serialization are unchanged:

| Existing id | Grounding |
| --- | --- |
| `canon.hina.basic.profile` | Hina's Gehenna third-year profile and position |
| `canon.hina.first_meeting_with_hoshino_vol1` | Confirmed direct meeting with timeline |
| `canon.relationship_closer_after_fight_with_set_and_hoshino` | Hoshino addressing Hina by name, after the described story events |

The last row's Set mention describes the scene, not a third participant in the reviewed
addressing relationship. The original wording is retained. It does **not** confirm how
Hina addresses Hoshino. We do not synthesize reverse addressing or a claim that no such
addressing exists.

Two rows live in `data/relationship_grounding.jsonl`, loaded explicitly by the grounder
with the **existing LoreIndex schema**. Default legacy lore loading never reads this file:

| New id | Evidence |
| --- | --- |
| `canon.hoshino.basic.profile` | Hoshino: Abydos High School, third year |
| `canon.hina_hoshino.same_school_year` | Reviewed comparison: different schools, same year; `fact_type=inference`, linked to both profile ids |

Hoshino school/year was checked on the [official game character page](https://bluearchive.jp/character)
on 2026-10-08: the Hoshino profile lists `アビドス高等学校` and `3年`.
Hina's existing accepted curated profile is reused. No extra age, personal relationship,
or Hina-to-Hoshino addressing assertion is added. The positive profile comparison prevents
reliance on age/rank inference without a character-specific negative instruction.
This is a retrieval regression fix in the opt-in path, **not a deployed answer-policy fix**.

Tests load and validate both corpora, check all new evidence links, select profiles plus
the comparison and direct evidence even with zero factual slots, preserve uncertainty
and timelines, and compare legacy results with the additive annotations removed.

## Follow-ups and merge boundaries

- **#313**: consumes the resolved request independently. No embedding backend, fusion,
  RRF, calibration, semantic thresholds, or ranking changes here. Grounder results should
  be composed after factual selection, retaining their own budget. Shared contract/core
  ranking files are untouched. `ai/retrieval_request.py` gets only additive bridge code;
  three existing corpus rows get metadata, so concurrent corpus edits may need resolution.
- **#315**: interpret the distinction between singleton background and exact pair evidence;
  bundle membership never proves local sufficiency. Add question-specific evidence and
  temporal sufficiency handling, directional addressing consumption, and web fallback.
  Timeline strings are preserved, not automatically ordered or matched to story epochs.
  Linked evidence is retained; runtime evidence expansion is not implemented.
- **#316**: shadow wiring, provenance-safe diagnostics, corpus/index lifecycle, model
  context policy activation, live Hoshino addressing evaluation, and reversible rollout.
  No query/conversation content was added to telemetry in this milestone.
