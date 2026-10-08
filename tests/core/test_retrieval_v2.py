import json
from dataclasses import replace

import pytest

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.knowledge_retrieval import (
    KnowledgeCandidate,
    RankedKnowledgeCandidate,
    lexical_search,
)
from hina_bot.core.knowledge_retrieval import (
    KnowledgeUsage as Usage,
)
from hina_bot.core.lore import LoreIndex, LoreValidationError, validate_record
from hina_bot.core.retrieval_v2 import (
    KnowledgeBundle,
    RetrievalRequest,
    UsageBudget,
    lexical_bundle,
)
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry


def candidate(identifier="test.fact", **overrides):
    return replace(KnowledgeCandidate(
        candidate_id=identifier, source="static_lore", reference=identifier,
        kind="world_fact", content="기준 내용", search_text="기준 내용",
        subjects=(), keywords=("기준",),
    ), **overrides)


def record(**overrides):
    row = {
        "id": "canon.test", "lane": "canon", "summary": "검수된 해석",
        "subjects": ["히나"], "keywords": ["휴식"], "fact_type": "inference",
        "knowledge": "inference", "confidence": "crosschecked", "status": "accepted",
        "kr_release": "confirmed", "timeline": "시점",
        "source": {"type": "curated", "title": "자료", "locator": "장면", "url": ""},
    }
    return row | overrides


@pytest.mark.parametrize("query", ["호시노", "생일", "대화 관계", "휴식", "zzzzzz"])
@pytest.mark.parametrize("limit,chars", [(0, 3200), (6, 0), (1, 100), (6, 3200)])
def test_packaged_factual_lexical_parity(query, limit, chars):
    index = LoreIndex.load()
    bundle = lexical_bundle(
        RetrievalRequest(query, query), index.candidates(include_community=False),
        budgets={Usage.FACTUAL: UsageBudget(limit, chars)},
    )
    assert bundle.context_sections()["facts"] == index.search(
        query, limit=limit, chars=chars, include_community=False,
    )
    assert not bundle.relations and not bundle.character_insights and not bundle.reactions


def test_usage_budgets_do_not_compete_and_support_multiple_usages():
    rows = [
        candidate("fact"),
        candidate("relation", usages=(Usage.RELATION,)),
        candidate("insight", kind="interpretation", usages=(Usage.FACTUAL, Usage.AMBIENT)),
        candidate("reaction", kind="optional_reaction"),
    ]
    bundle = lexical_bundle(
        RetrievalRequest("기준", "기준"), iter(rows),
        budgets={usage: UsageBudget(1, 1000) for usage in Usage},
    )
    assert bundle.facts[0].candidate.candidate_id == "fact"
    assert bundle.relations[0].candidate.candidate_id == "relation"
    assert bundle.character_insights[0].candidate.candidate_id == "insight"
    assert bundle.reactions[0].candidate.candidate_id == "reaction"
    assert set(bundle.context_sections()) == {"facts", "relations", "character_insights", "reactions"}


def test_budget_skip_threshold_disabled_and_zero_result():
    short = candidate("short")
    long = candidate("long", content="가" * 1000)
    size = len(json.dumps(short.reference_item(), ensure_ascii=False))
    request = RetrievalRequest("기준", "기준")
    bundle = lexical_bundle(request, [long, short], budgets={Usage.FACTUAL: UsageBudget(2, size)})
    assert [row.candidate.candidate_id for row in bundle.facts] == ["short"]
    score = bundle.facts[0].score
    assert not lexical_bundle(
        request, [short], budgets={Usage.FACTUAL: UsageBudget(2, size, min_score=score)},
    ).facts
    assert lexical_bundle(request, [short], budgets={}) == KnowledgeBundle()
    assert lexical_bundle(
        RetrievalRequest("zzzzz", "zzzzz"), [short],
        budgets={usage: UsageBudget(2, 1000) for usage in Usage},
    ) == KnowledgeBundle()


def test_provenance_is_not_a_ranking_tier_and_unknown_is_not_promoted():
    static = candidate("static", fact_type="unknown", kind="interpretation",
                       awareness="unknown", confidence="crosschecked",
                       metadata=(("guard", "do_not_assert_positive_fact"),))
    runtime = candidate("runtime", source="runtime_knowledge")
    bundle = lexical_bundle(
        RetrievalRequest("기준", "기준"), [static, runtime],
        budgets={Usage.FACTUAL: UsageBudget(2, 1000)},
    )
    assert [row.candidate.source for row in bundle.facts] == ["static_lore", "runtime_knowledge"]
    assert bundle.context_sections()["facts"][0]["kind"] == "interpretation"
    assert bundle.context_sections()["facts"][0]["guard"] == "do_not_assert_positive_fact"


def test_bundle_rejects_wrong_usage():
    with pytest.raises(ValueError):
        KnowledgeBundle(relations=(RankedKnowledgeCandidate(1, 0, candidate()),))


def test_static_metadata_preserved_without_changing_legacy_reference():
    original = record()
    enriched = record(
        usage=["factual", "ambient"], entities=["character.hina"],
        semantic_text="책임과 휴식의 긴장", evidence_ids=["canon.hina.rest"],
        # Existing tooling's evidence is a text excerpt, not a list of ids.
        evidence="장면 원문",
    )
    validate_record(enriched, accepted=True)
    row = LoreIndex([enriched]).candidates()[0]
    assert row.reference_item() == LoreIndex([original]).candidates()[0].reference_item()
    assert row.retrieval_usages == (Usage.FACTUAL, Usage.AMBIENT)
    assert row.entities == ("character.hina",)
    assert row.evidence_ids == ("canon.hina.rest",)
    assert row.semantic_representation == enriched["semantic_text"]
    assert dict(row.source_metadata) == enriched["source"]
    assert (row.fact_type, row.confidence, row.lane, row.kr_release, row.awareness, row.time) == (
        "inference", "crosschecked", "canon", "confirmed", "inference", "시점",
    )


@pytest.mark.parametrize("extra", [
    {"usage": []}, {"usage": "both"}, {"usage": ["factual", "factual"]},
    {"usage": "reaction"}, {"usage": "ambient", "fact_type": "unknown"},
    {"usage": "ambient", "fact_type": "fact_direct"},
    {"entities": "character.hina"}, {"entities": ["히나"]},
    {"evidence_ids": ["canon.test", "canon.test"]},
    {"semantic_text": ""}, {"semantic_text": None},
])
def test_invalid_additive_metadata_rejected(extra):
    with pytest.raises(LoreValidationError):
        validate_record(record(**extra), accepted=True)


def test_existing_inference_is_not_automatically_ambient():
    row = LoreIndex([record()]).candidates()[0]
    assert row.retrieval_usages == (Usage.FACTUAL,)
    assert row.semantic_representation == row.search_text
    assert not lexical_bundle(
        RetrievalRequest("휴식", "휴식"), [row], budgets={Usage.AMBIENT: UsageBudget(2, 1000)},
    ).character_insights


def test_in_memory_legacy_rows_can_omit_review_provenance():
    legacy = record()
    for key in ("confidence", "kr_release", "source"):
        legacy.pop(key)
    row = LoreIndex([legacy]).candidates()[0]
    assert row.confidence is None and row.kr_release is None and not row.source_metadata
    assert row.reference_item()["awareness"] == legacy["knowledge"]


def test_reaction_parity_and_provenance_stays_out_of_model_reference():
    meme = record(
        id="meme.test", lane="community_meme", kr_release="not_applicable",
        fact_type="fandom", reaction="짧은 반응", knowledge="unknown",
    )
    validate_record(meme, accepted=True)
    index = LoreIndex([meme])
    bundle = lexical_bundle(
        RetrievalRequest("휴식", "휴식"), index.candidates(),
        budgets={usage: UsageBudget(2, 1000) for usage in Usage},
    )
    assert bundle.context_sections()["reactions"] == index.search("휴식")
    assert bundle.reactions[0].candidate.lane == "community_meme"
    assert bundle.reactions[0].candidate.awareness == "unknown"
    assert bundle.reactions[0].candidate.time == meme["timeline"]
    assert bundle.context_sections()["reactions"] == [
        {"kind": "optional_reaction", "content": "짧은 반응"},
    ]
    assert not bundle.facts
    with pytest.raises(LoreValidationError):
        validate_record(meme | {"usage": "factual"}, accepted=True)


@pytest.mark.parametrize("kind,awareness", [("world_fact", "self"), ("interpretation", "inference")])
def test_runtime_registry_compatibility_without_synthesized_certainty(kind, awareness):
    database = AdminDatabase(":memory:")
    try:
        registry = RuntimeKnowledgeRegistry(database, kind=kind)
        registry.add("test.entry", "휴식 관련 내용", "휴식", "히나", awareness, "장면")
        registry.add("disabled", "휴식", "휴식", "히나", awareness)
        registry.set_enabled("disabled", False)
        rows = registry.candidates()
        bundle = lexical_bundle(
            RetrievalRequest("휴식", "휴식"), rows,
            budgets={usage: UsageBudget(2, 1000) for usage in Usage},
        )
        assert bundle.context_sections()["facts"] == registry.search("휴식", limit=2, chars=1000)
        row = bundle.facts[0].candidate
        assert len(rows) == 1
        assert row.confidence is None and row.lane is None and row.kr_release is None
        assert not row.entities and not row.evidence_ids and not row.source_metadata
        assert not bundle.character_insights
        assert row.fact_type == ("inference" if kind == "interpretation" else None)
        assert row.awareness == awareness and row.time == "장면"
    finally:
        database.close()


def test_semantic_representation_never_repeats_keywords_or_subjects():
    row = candidate(subjects=("name",), keywords=("name",) * 10)
    assert row.semantic_representation == row.search_text
    assert "name" not in row.semantic_representation
    assert replace(row, semantic_text="reviewed meaning").semantic_representation == "reviewed meaning"


def test_single_usage_adapter_preserves_tie_order_and_scores():
    rows = [candidate("first"), candidate("second")]
    request = RetrievalRequest("기준", "기준")
    bundle = lexical_bundle(request, rows, budgets={Usage.FACTUAL: UsageBudget(2, 1000)})
    assert bundle.context_sections()["facts"] == lexical_search("기준", rows, limit=2, chars=1000)
    assert bundle.facts[0].score == bundle.facts[1].score
    assert [row.order for row in bundle.facts] == [0, 1]
