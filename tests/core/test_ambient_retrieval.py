import asyncio
from dataclasses import replace
from math import sqrt

import pytest

from hina_bot.core.ambient_retrieval import (
    AmbientConfig,
    AmbientRetriever,
    AmbientSceneContext,
    ambient_query,
    eligible_ambient_candidates,
)
from hina_bot.core.knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
)
from hina_bot.core.lore import LoreIndex
from hina_bot.core.retrieval_v2 import (
    BundleComposer,
    RetrievalIntent,
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
    cache_key = "ambient-fake:v1:2"
    dimensions = 2

    def __init__(self):
        self.documents = []
        self.queries = []
        self.failure = False
        self.vectors = {
            "rest": (1, 0),
            "comfort": (0.98, sqrt(1 - 0.98**2)),
            "leisure": (0.8, 0.6),
            "weak": (0.6, 0.8),
            "other": (0, 1),
        }

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        if self.failure:
            raise RuntimeError("provider detail must not escape")
        return EmbeddingResult(
            tuple(self.vectors[text] for text in texts),
            EmbeddingUsage(31, 1),
        )

    async def embed_query(self, text):
        self.queries.append(text)
        if self.failure:
            raise RuntimeError("provider detail must not escape")
        vector = (0, 1) if "무관한 잡담" in text else (1, 0)
        return EmbeddingResult((vector,), EmbeddingUsage(9, 1))


def request(text="오늘은 일 그만하고 좀 쉬어도 되잖아", **kwargs):
    return RetrievalRequest(
        text,
        "lexical expansion must never enter ambient query",
        intent=RetrievalIntent.CONVERSATION,
        **kwargs,
    )


def scene(**kwargs):
    return AmbientSceneContext(rp_entity=HINA, **kwargs)


def candidate(identifier="rest", **kwargs):
    return replace(KnowledgeCandidate(
        candidate_id=identifier,
        source="static_lore",
        kind="interpretation",
        content=identifier,
        search_text=identifier,
        semantic_text=identifier,
        subjects=("히나",),
        keywords=(identifier,),
        lane="canon",
        usages=(KnowledgeUsage.FACTUAL, KnowledgeUsage.AMBIENT),
        entities=(HINA,),
        fact_type="inference",
        confidence="crosschecked",
        kr_release="confirmed",
    ), **kwargs)


def retriever(backend=None, **kwargs):
    backend = backend or FakeBackend()
    config = AmbientConfig(
        calibration=SemanticCalibration(backend.cache_key, 0.5, 0.9),
        **kwargs,
    )
    return AmbientRetriever(SemanticIndex(backend), config=config), backend


def ids(result):
    return [row.candidate.candidate_id for row in result.rows]


def test_ambient_query_is_bounded_scene_context_not_lexical_or_canonical_ids():
    req = request(
        "지금은 좀 쉬면 안 돼?",
        anchor_text="계속 일만 했잖아",
        entities=(HINA,),
    )
    ctx = scene(
        recent_same_speaker=(
            "이건 가장 오래된 턴이라 빠져야 해",
            "아까부터 계속 피곤해 보였어",
            "오늘은 일을 더 하지 말자",
        ),
        relationship_signal="상대와 편안하게 사적인 대화를 나누는 관계",
    )
    query = ambient_query(req, ctx)
    assert "지금은 좀 쉬면 안 돼?" in query
    assert "계속 일만 했잖아" in query
    assert "아까부터 계속 피곤해 보였어" in query
    assert "오늘은 일을 더 하지 말자" in query
    assert "편안하게 사적인 대화" in query
    assert "가장 오래된 턴" not in query
    assert req.retrieval_text not in query
    assert HINA not in query


def test_ambient_query_deduplicates_same_meaning_anchor_and_recent_turn():
    req = request("오늘은 쉬자", anchor_text="오늘은 쉬자!")
    ctx = scene(recent_same_speaker=("오늘은 쉬자.",))
    assert ambient_query(req, ctx) == "오늘은 쉬자"


def test_entity_and_usage_are_hard_candidate_filters():
    rows = [
        candidate("rest"),
        candidate("other-entity", entities=("character.hoshino",)),
        candidate("factual-only", usages=(KnowledgeUsage.FACTUAL,)),
        candidate("not-inference", fact_type="fact_direct"),
    ]
    selected = eligible_ambient_candidates(scene(), rows)
    assert [row.candidate_id for row in selected] == ["rest"]


async def test_precision_first_semantic_retrieval_and_shared_two_item_budget():
    engine, backend = retriever()
    rows = [
        candidate("rest"),
        candidate("comfort"),
        candidate("leisure"),
        candidate("weak"),
        candidate("other"),
    ]
    result = await engine.retrieve(request(), scene(), rows)
    assert ids(result) == ["rest", "comfort", "leisure"]
    assert result.semantic_status == "available"
    assert len(backend.queries) == 1
    assert set(backend.documents) == {"rest", "comfort", "leisure", "weak", "other"}

    bundle = BundleComposer({
        KnowledgeUsage.AMBIENT: UsageBudget(2, 1000),
    }).compose({
        KnowledgeUsage.AMBIENT: result.rows,
    })
    assert [
        row.candidate.candidate_id for row in bundle.character_insights
    ] == ["rest", "comfort"]
    assert all(
        item["kind"] == "interpretation"
        for item in bundle.context_sections()["character_insights"]
    )


async def test_unrelated_ordinary_chat_normally_returns_zero_insights():
    engine, _ = retriever()
    result = await engine.retrieve(
        request("무관한 잡담인데 점심 뭐 먹었어?"),
        scene(),
        [candidate("rest"), candidate("comfort")],
    )
    assert result.semantic_status == "available"
    assert not result.rows


async def test_missing_calibration_or_backend_yields_zero_without_lexical_fallback():
    backend = FakeBackend()
    rows = [candidate("rest", keywords=("쉬어",) * 20)]

    without_calibration = AmbientRetriever(SemanticIndex(backend))
    result = await without_calibration.retrieve(request("쉬어"), scene(), rows)
    assert result.semantic_status == "unavailable"
    assert not result.rows
    assert not backend.queries and not backend.documents

    without_backend = AmbientRetriever(config=AmbientConfig(
        calibration=SemanticCalibration(backend.cache_key, 0.5, 0.9),
    ))
    result = await without_backend.retrieve(request("쉬어"), scene(), rows)
    assert result.semantic_status == "unavailable"
    assert not result.rows


async def test_backend_failure_is_zero_ambient_context_not_answer_failure():
    backend = FakeBackend()
    backend.failure = True
    engine, _ = retriever(backend)
    result = await engine.retrieve(
        request("쉬어"),
        scene(),
        [candidate("rest", keywords=("쉬어",) * 20)],
    )
    assert result.semantic_status == "failed"
    assert not result.rows
    assert "provider detail" not in repr(result)


async def test_retrieval_does_not_suppress_same_relevant_insight_across_turns():
    engine, backend = retriever()
    rows = [candidate("rest")]
    first = await engine.retrieve(request("오늘은 쉬자"), scene(), rows)
    second = await engine.retrieve(request("이번엔 정말 쉬어"), scene(), rows)
    assert ids(first) == ids(second) == ["rest"]
    assert backend.documents == ["rest"]
    assert len(backend.queries) == 2
    assert second.cache_hits == 1


async def test_cancellation_propagates():
    class Slow(FakeBackend):
        async def embed_query(self, text):
            await asyncio.Event().wait()

    engine, _ = retriever(Slow(), timeout_seconds=30)
    task = asyncio.create_task(
        engine.retrieve(request(), scene(), [candidate("rest")])
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_packaged_ambient_rows_are_reviewed_hina_inferences_with_valid_evidence_links():
    rows = LoreIndex.load().candidates(include_community=False)
    ambient = [
        row for row in rows
        if KnowledgeUsage.AMBIENT in row.retrieval_usages
    ]
    assert {row.candidate_id for row in ambient} == {
        "canon.hina.unfamiliar-with-ordinary-leisure.inference",
        "canon.hina.relaxes-around-teacher.inference",
        "canon.hina.relationship.inference.cautious_desire_spend_time",
        "canon.hina.dress.inference.private-time-importance",
        "canon.hina.rest_discomfort_pattern",
    }
    ids = {row.candidate_id for row in rows}
    for row in ambient:
        assert row.fact_type == "inference"
        assert row.kind == "interpretation"
        assert row.entities == (HINA,)
        assert KnowledgeUsage.FACTUAL in row.retrieval_usages
        assert row.evidence_ids
        assert set(row.evidence_ids) <= ids


@pytest.mark.parametrize("rp_entity", ["", "hina", "character.Hina", "character.hina!"])
def test_scene_requires_canonical_entity_id(rp_entity):
    with pytest.raises(ValueError):
        AmbientSceneContext(rp_entity=rp_entity)
