from dataclasses import replace

import pytest

from hina_bot.core.ambient_retrieval import (
    AmbientConfig,
    AmbientRetriever,
    ambient_query,
    eligible_ambient_candidates,
)
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate, KnowledgeUsage
from hina_bot.core.lore import LoreIndex
from hina_bot.core.retrieval_v2 import (
    BundleComposer,
    RetrievalRequest,
    UsageBudget,
)
from hina_bot.core.semantic_retrieval import (
    EmbeddingResult,
    EmbeddingUsage,
    SemanticCalibration,
    SemanticIndex,
)

HINA = "character.hina"


class FakeBackend:
    cache_key = "fake:ambient:v1:3"
    dimensions = 3

    def __init__(self):
        self.documents = []
        self.queries = []
        self.fail = False
        self.vectors = {
            "rest": (1, 0, 0),
            "leisure": (0, 1, 0),
            "praise": (0, 0, 1),
        }

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        if self.fail:
            raise RuntimeError("provider content must not escape")
        return EmbeddingResult(
            tuple(self.vectors[text] for text in texts),
            EmbeddingUsage(30, 1),
        )

    async def embed_query(self, text):
        self.queries.append(text)
        if self.fail:
            raise RuntimeError("provider content must not escape")
        if "쉬어" in text or "휴식" in text:
            vector = (1, 0, 0)
        elif "쇼핑" in text or "놀자" in text:
            vector = (0, 1, 0)
        elif "칭찬" in text or "잘했" in text:
            vector = (0, 0, 1)
        else:
            vector = (-1, 0, 0)
        return EmbeddingResult((vector,), EmbeddingUsage(10, 1))


def request(
    visible="오늘은 일 좀 내려놓고 나랑 쉬어도 되잖아.",
    *,
    anchor="",
    retrieval="lexical 확장 단어는 ambient query에 들어가면 안 됨",
):
    return RetrievalRequest(
        visible,
        retrieval,
        anchor_text=anchor,
        entities=(HINA,),
    )


def ambient_candidate(identifier="rest", **overrides):
    return replace(
        KnowledgeCandidate(
            candidate_id=identifier,
            source="static_lore",
            kind="interpretation",
            content=identifier,
            search_text=identifier,
            subjects=("히나",),
            keywords=(),
            entities=(HINA,),
            usages=(KnowledgeUsage.FACTUAL, KnowledgeUsage.AMBIENT),
            lane="canon",
            fact_type="inference",
            awareness="inference",
            semantic_text=identifier,
            evidence_ids=("canon.evidence",),
        ),
        **overrides,
    )


def retriever():
    backend = FakeBackend()
    config = AmbientConfig(
        calibration=SemanticCalibration(backend.cache_key, 0.5, 0.9),
        min_score=0.75,
    )
    return AmbientRetriever(SemanticIndex(backend), config=config), backend


def ids(result):
    return [row.candidate.candidate_id for row in result.rows]


def test_scene_query_is_bounded_semantic_context_not_lexical_or_entity_text():
    req = request(anchor="어제도 밤새 일했다고 했지")
    query = ambient_query(
        req,
        recent_same_speaker=("좀 피곤하다", "그래도 오늘 할 일은 해야지"),
        relationship_axes={"familiarity": 3, "comfort": 2, "support_openness": 4},
    )
    assert "좀 피곤하다" in query
    assert "그래도 오늘 할 일은 해야지" in query
    assert "어제도 밤새 일했다고 했지" in query
    assert "오늘은 일 좀 내려놓고" in query
    assert "익숙함 3/4" in query and "편안함 2/4" in query
    assert "lexical 확장" not in query
    assert HINA not in query
    assert len(query) <= 1200


def test_scene_query_deduplicates_anchor_and_limits_recent_context():
    req = request(
        visible="그럼 오늘은 쉬어.",
        anchor="그럼 오늘은 쉬어.",
    )
    query = ambient_query(req, recent_same_speaker=("그럼 오늘은 쉬어.",))
    assert query.count("그럼 오늘은 쉬어.") == 1
    with pytest.raises(ValueError):
        ambient_query(req, recent_same_speaker=("a", "b", "c"))
    with pytest.raises(ValueError):
        ambient_query(req, relationship_axes={"unknown": 1})
    with pytest.raises(ValueError):
        ambient_query(req, relationship_axes={"comfort": 5})


def test_ambient_candidate_filter_is_precision_first_and_entity_scoped():
    good = ambient_candidate()
    rows = [
        good,
        ambient_candidate("wrong-entity", entities=("character.hoshino",)),
        ambient_candidate("wrong-usage", usages=(KnowledgeUsage.FACTUAL,)),
        ambient_candidate("wrong-kind", fact_type="fact_direct"),
        ambient_candidate("audience-only", awareness="audience_only"),
        ambient_candidate("meme", lane="community_meme"),
    ]
    assert eligible_ambient_candidates(rows) == [good]


async def test_semantic_only_retrieval_returns_scene_relevant_rows_and_zero_for_unrelated():
    engine, backend = retriever()
    rows = [
        ambient_candidate("rest"),
        ambient_candidate("leisure"),
        ambient_candidate("praise"),
    ]

    rest = await engine.retrieve(request(), rows)
    assert ids(rest) == ["rest"]
    assert rest.semantic_status == "available"

    shopping = await engine.retrieve(
        request("오늘은 업무 말고 그냥 같이 쇼핑하고 놀자."),
        rows,
    )
    assert ids(shopping) == ["leisure"]

    unrelated = await engine.retrieve(request("오늘 점심 뭐 먹었어?"), rows)
    assert not unrelated.rows
    assert len(backend.queries) == 3


async def test_anchor_recent_turns_and_relationship_signal_can_shape_scene_query():
    engine, backend = retriever()
    rows = [ambient_candidate("rest"), ambient_candidate("leisure")]
    result = await engine.retrieve(
        request("그럼 같이 놀자.", anchor="요즘 계속 일만 했잖아"),
        rows,
        recent_same_speaker=("오늘도 일이 많네",),
        relationship_axes={"comfort": 3, "support_openness": 2},
    )
    assert ids(result) == ["leisure"]
    query = backend.queries[-1]
    assert "요즘 계속 일만 했잖아" in query
    assert "오늘도 일이 많네" in query
    assert "편안함 3/4" in query


async def test_no_calibration_or_backend_failure_degrades_to_no_insight_without_fallback():
    backend = FakeBackend()
    unavailable = AmbientRetriever(SemanticIndex(backend))
    result = await unavailable.retrieve(request(), [ambient_candidate()])
    assert not result.rows and result.semantic_status == "unavailable"
    assert not backend.queries and not backend.documents

    engine, backend = retriever()
    backend.fail = True
    failed = await engine.retrieve(request(), [ambient_candidate()])
    assert not failed.rows and failed.semantic_status == "failed"
    assert "provider content" not in repr(failed)


async def test_repeated_scene_can_retrieve_same_insight_without_repetition_state():
    engine, _ = retriever()
    rows = [ambient_candidate()]
    first = await engine.retrieve(request(), rows)
    second = await engine.retrieve(request(), rows)
    assert ids(first) == ids(second) == ["rest"]
    assert second.cache_hits == 1


async def test_bundle_composer_keeps_ambient_budget_small_and_deduplicates():
    engine, _ = retriever()
    rows = [
        ambient_candidate("rest"),
        ambient_candidate("leisure"),
        ambient_candidate("praise"),
    ]
    result = await engine.retrieve(
        request("오늘은 쉬어. 칭찬도 해줄게."),
        rows,
    )
    bundle = BundleComposer({
        KnowledgeUsage.AMBIENT: UsageBudget(2, 900),
    }).compose({
        KnowledgeUsage.AMBIENT: result.rows,
    })
    assert len(bundle.character_insights) <= 2
    assert not bundle.facts and not bundle.relations and not bundle.reactions


def test_packaged_ambient_corpus_is_reviewed_inference_with_evidence_links():
    index = LoreIndex.load()
    records = {row["id"]: row for row in index.records}
    ambient = [
        candidate
        for candidate in index.candidates(include_community=False)
        if KnowledgeUsage.AMBIENT in candidate.retrieval_usages
    ]
    expected = {
        "canon.hina.rest_discomfort_pattern",
        "canon.hina.unfamiliar-with-ordinary-leisure.inference",
        "canon.hina.relaxes-around-teacher.inference",
        "canon.hina.relationship.inference.cautious_desire_spend_time",
        "canon.hina.values_praise_and_attention_in_private_time",
    }
    assert {row.candidate_id for row in ambient} == expected
    assert all(row.fact_type == "inference" for row in ambient)
    assert all(row.awareness == "inference" for row in ambient)
    assert all(row.entities == (HINA,) for row in ambient)
    assert all(
        set(row.evidence_ids) <= set(records) and row.evidence_ids
        for row in ambient
    )
