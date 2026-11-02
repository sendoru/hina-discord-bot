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
from hina_bot.core.retrieval_contracts import KnowledgeBundle
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


def test_lexical_search_keeps_unknown_guards_without_promoting_them():
    static = candidate("static", fact_type="unknown", kind="interpretation",
                       awareness="unknown", confidence="crosschecked",
                       metadata=(("guard", "do_not_assert_positive_fact"),))
    runtime = candidate("runtime", source="runtime_knowledge")
    rows = lexical_search("기준", [static, runtime], limit=2, chars=1000)
    assert [row["reference"] for row in rows] == ["static", "runtime"]
    assert rows[0]["kind"] == "interpretation"
    assert rows[0]["guard"] == "do_not_assert_positive_fact"


def test_bundle_rejects_wrong_usage():
    with pytest.raises(ValueError):
        KnowledgeBundle(relations=(RankedKnowledgeCandidate(1, 0, candidate()),))


def test_static_metadata_preserved_without_changing_legacy_reference():
    original = record()
    enriched = record(
        usage=["factual", "ambient"], entities=["character.hina"],
        evidence_ids=["canon.hina.rest"],
        # Existing tooling's evidence is a text excerpt, not a list of ids.
        evidence="장면 원문",
    )
    validate_record(enriched, accepted=True)
    row = LoreIndex([enriched]).candidates()[0]
    assert row.reference_item() == LoreIndex([original]).candidates()[0].reference_item()
    assert row.retrieval_usages == (Usage.FACTUAL, Usage.AMBIENT)
    assert row.entities == ("character.hina",)
    assert row.evidence_ids == ("canon.hina.rest",)
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
])
def test_invalid_additive_metadata_rejected(extra):
    with pytest.raises(LoreValidationError):
        validate_record(record(**extra), accepted=True)


def test_existing_inference_is_not_automatically_ambient():
    row = LoreIndex([record()]).candidates()[0]
    assert row.retrieval_usages == (Usage.FACTUAL,)
    with pytest.raises(ValueError):
        KnowledgeBundle(character_insights=(RankedKnowledgeCandidate(1, 0, row),))


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
    [row] = index.candidates()
    assert row.retrieval_usages == (Usage.REACTION,)
    assert row.lane == "community_meme"
    assert row.awareness == "unknown"
    assert row.time == meme["timeline"]
    # Provenance stays out of what the model sees.
    assert index.search("휴식") == [{"kind": "optional_reaction", "content": "짧은 반응"}]
    with pytest.raises(LoreValidationError):
        validate_record(meme | {"usage": "factual"}, accepted=True)


@pytest.mark.parametrize("kind,awareness", [("world_fact", "self"), ("interpretation", "inference")])
def test_runtime_registry_compatibility_without_synthesized_certainty(kind, awareness):
    database = AdminDatabase.create(":memory:")
    try:
        registry = RuntimeKnowledgeRegistry(database, kind=kind)
        registry.add("test.entry", "휴식 관련 내용", "휴식", "히나", awareness, "장면")
        registry.add("disabled", "휴식", "휴식", "히나", awareness)
        registry.set_enabled("disabled", False)
        rows = registry.candidates()
        assert len(rows) == 1
        row = rows[0]
        # The disabled entry stays out of both candidates and search results.
        assert [item["reference"] for item in registry.search("휴식", limit=2, chars=1000)] == [
            row.reference
        ]
        assert row.confidence is None and row.lane is None and row.kr_release is None
        assert not row.entities and not row.evidence_ids and not row.source_metadata
        assert Usage.AMBIENT not in row.retrieval_usages
        assert row.fact_type == ("inference" if kind == "interpretation" else None)
        assert row.awareness == awareness and row.time == "장면"
    finally:
        database.close()
