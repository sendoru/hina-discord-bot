import asyncio
from dataclasses import replace
from math import sqrt

import pytest

from hina_bot.core.hybrid_retrieval import Fusion, HybridConfig, HybridRetriever
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate, rank_lexical_candidates
from hina_bot.core.retrieval_v2 import RetrievalIntent, RetrievalRequest, UsageBudget
from hina_bot.core.semantic_retrieval import (
    EmbeddingResult,
    EmbeddingUsage,
    SemanticCalibration,
    SemanticIndex,
    normalize,
    semantic_query,
)


class FakeBackend:
    """Synthetic geometry tests mechanics ONLY; never a calibration/recall measurement."""

    cache_key = "fake:v1:2"
    dimensions = 2

    def __init__(self):
        self.documents = []
        self.queries = []
        self.failure = False
        self.query_failure = False
        self.vectors = {"positive": (1, 0), "negative": (0.4, sqrt(0.84)),
                        "unrelated": (0, 1)}

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        if self.failure:
            raise RuntimeError("sensitive backend response")
        return EmbeddingResult(tuple(self.vectors[text] for text in texts), EmbeddingUsage(37, 1))

    async def embed_query(self, text):
        self.queries.append(text)
        if self.query_failure:
            raise RuntimeError("query failed")
        return EmbeddingResult(((1, 0),), EmbeddingUsage(11, 1))


def candidate(identifier="positive", **kwargs):
    return replace(KnowledgeCandidate(
        candidate_id=identifier, source="static_lore", kind="world_fact",
        content=identifier, search_text=identifier, subjects=(), keywords=(),
    ), **kwargs)


def request(text="표현이 완전히 다른 질문", **kwargs):
    return RetrievalRequest(text, text, intent=RetrievalIntent.FACT, **kwargs)


def retriever(backend=None, **kwargs):
    backend = backend or FakeBackend()
    config = HybridConfig(calibration=SemanticCalibration(backend.cache_key, 0.5, 0.9), **kwargs)
    return HybridRetriever(SemanticIndex(backend), config=config), backend


def ids(result):
    return [row.candidate.candidate_id for row in result.bundle.facts]


BUDGET = UsageBudget(3, 3200)


@pytest.mark.parametrize("fusion", list(Fusion))
async def test_paraphrase_recall_and_overlap_hard_negative_rejection(fusion):
    engine, backend = retriever(fusion=fusion)
    rows = [candidate("negative", keywords=("완전히", "질문")), candidate()]
    assert rank_lexical_candidates(request().retrieval_text, rows)[0].candidate == rows[0]
    result = await engine.retrieve(request(), rows, budget=BUDGET)
    assert ids(result) == ["positive"]
    assert len(backend.queries) == 1
    assert result.semantic_status == "available"


async def test_unrelated_factual_query_can_have_zero_results():
    engine, _ = retriever()
    result = await engine.retrieve(request(), [candidate("unrelated")], budget=BUDGET)
    assert not result.bundle.facts


async def test_default_without_calibration_makes_no_calls():
    backend = FakeBackend()
    engine = HybridRetriever(SemanticIndex(backend))
    result = await engine.retrieve(request("생일"), [candidate(keywords=("생일",))], budget=BUDGET)
    assert not backend.queries and not backend.documents
    assert result.semantic_status == "unavailable"
    assert not result.bundle.facts  # a weak single token must not fill a slot


@pytest.mark.parametrize("field", ["생일", "학년", "무기", "소속", "직책"])
async def test_profile_preserves_legacy_order_and_skips_semantics(field):
    engine, backend = retriever()
    req = RetrievalRequest(field, field, intent=RetrievalIntent.PROFILE)
    rows = [candidate("negative", keywords=(field,) * 4),
            candidate("positive", keywords=(field,) * 5)]
    result = await engine.retrieve(req, rows, budget=BUDGET)
    assert ids(result) == [r.candidate.candidate_id for r in rank_lexical_candidates(field, rows)]
    assert not backend.queries and not backend.documents


async def test_configured_strong_lexical_hit_skips_semantics():
    engine, backend = retriever(skip_semantic_at_lexical=14)
    result = await engine.retrieve(request("생일"),
                                  [candidate(keywords=("생일",) * 2)], budget=BUDGET)
    assert ids(result) == ["positive"]
    assert result.semantic_status == "lexical_short_circuit"
    assert not backend.queries


@pytest.mark.parametrize("stage", ["failure", "query_failure"])
async def test_backend_failure_uses_thresholded_lexical_fallback(stage):
    engine, backend = retriever()
    setattr(backend, stage, True)
    result = await engine.retrieve(request("생일"),
                                  [candidate(keywords=("생일",) * 2)], budget=BUDGET)
    assert ids(result) == ["positive"]
    assert result.semantic_status == "failed"
    assert "sensitive" not in repr(result)


@pytest.mark.parametrize("failure", [False, True])
async def test_required_entities_gate_all_channels_including_failure(failure):
    engine, backend = retriever()
    backend.failure = failure
    entities = ("character.hina", "character.hoshino")
    req = request("생일", entities=entities, required_entities=entities)
    rows = [candidate("bad", semantic_text="positive", entities=entities[:1],
                      keywords=("생일",) * 10),
            candidate(entities=entities, keywords=("생일",) * 2)]
    result = await engine.retrieve(req, rows, budget=BUDGET)
    assert ids(result) == ["positive"]
    assert backend.documents == ["positive"]


async def test_conversation_disabled_usage_and_empty_budget_skip_embedding():
    engine, backend = retriever()
    req = replace(request(), intent=RetrievalIntent.CONVERSATION)
    assert not (await engine.retrieve(req, [candidate()], budget=BUDGET)).bundle.facts
    assert not (await engine.retrieve(request(), [candidate()], budget=UsageBudget(0, 10))).bundle.facts
    assert not (await engine.retrieve(request(), [candidate(kind="optional_reaction")],
                                      budget=BUDGET)).bundle.facts
    assert not backend.documents and not backend.queries


async def test_sources_are_not_priorities_and_ties_are_stable():
    engine, _ = retriever()
    rows = [candidate("one", source="static_lore", semantic_text="positive"),
            candidate("two", source="runtime_knowledge", semantic_text="positive")]
    assert ids(await engine.retrieve(request(), rows, budget=BUDGET)) == ["one", "two"]
    assert ids(await engine.retrieve(request(), rows[::-1], budget=BUDGET)) == ["two", "one"]


async def test_current_reference_and_guard_are_kept_when_embedding_is_reused():
    engine, backend = retriever()
    await engine.retrieve(request(), [candidate()], budget=BUDGET)
    changed = candidate(kind="interpretation", metadata=(("guard", "do_not_assert_positive_fact"),))
    result = await engine.retrieve(request(), [changed], budget=BUDGET)
    assert result.bundle.facts[0].candidate == changed
    assert backend.documents == ["positive"]
    assert result.cache_hits == 1 and result.cache_misses == 0


async def test_representation_cache_invalidation_and_removal():
    backend = FakeBackend()
    index = SemanticIndex(backend)
    rows = [candidate(subjects=("반복",) * 10, keywords=("반복",) * 10),
            candidate("runtime", source="runtime_knowledge", semantic_text="negative")]
    await index.search("question", rows, top_k=3)
    await index.search("another question", rows, top_k=3)
    assert backend.documents == ["positive", "negative"]
    assert len(backend.queries) == 2
    changed = [replace(rows[0], semantic_text="unrelated"),
               replace(rows[1], search_text="unrelated", semantic_text=None)]
    result = await index.search("question", changed, top_k=3)
    assert backend.documents == ["positive", "negative", "unrelated"]
    assert result.cache_misses == 1  # identical meaning text shared across sources
    assert len((await index.search("question", changed[:1], top_k=3)).hits) == 1
    backend.cache_key = "fake:v2:2"
    await index.search("question", changed[:1], top_k=3)
    assert backend.documents[-2:] == ["unrelated", "unrelated"]


async def test_calibration_model_mismatch_falls_back_without_calls():
    engine, backend = retriever()
    backend.cache_key = "new-model"
    result = await engine.retrieve(request("생일"),
                                  [candidate(keywords=("생일",) * 2)], budget=BUDGET)
    assert result.semantic_status == "calibration_mismatch"
    assert ids(result) == ["positive"]
    assert not backend.queries and not backend.documents


async def test_concurrent_fill_and_bounded_cache():
    backend = FakeBackend()
    index = SemanticIndex(backend, max_entries=1)
    await asyncio.gather(*(index.search("q", [candidate()], top_k=1) for _ in range(3)))
    assert backend.documents == ["positive"] and len(backend.queries) == 3
    await index.warm([candidate("negative")])
    await index.warm([candidate()])
    assert backend.documents == ["positive", "negative", "positive"]
    assert len(backend.queries) == 3


async def test_matrix_larger_than_cache_remains_complete():
    index = SemanticIndex(FakeBackend(), max_entries=1)
    result = await index.search("q", [candidate(), candidate("negative")], top_k=2)
    assert len(result.hits) == 2 and result.hits[0].order == 0


async def test_timeout_and_cancellation():
    class Slow(FakeBackend):
        async def embed_query(self, text):
            await asyncio.Event().wait()
    engine, _ = retriever(Slow(), timeout_seconds=0.01)
    result = await engine.retrieve(request("생일"),
                                  [candidate(keywords=("생일",) * 2)], budget=BUDGET)
    assert result.semantic_status == "failed" and ids(result) == ["positive"]
    task = asyncio.create_task(engine.retrieve(request(), [candidate()], budget=BUDGET))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("vector,dimensions", [([], 2), ([1], 2), ([0, 0], 2),
                                              ([float('nan'), 0], 2), ([float('inf'), 1], 2)])
def test_invalid_embeddings(vector, dimensions):
    with pytest.raises(ValueError):
        normalize(vector, dimensions)


async def test_malformed_backend_does_not_poison_cache():
    class Broken(FakeBackend):
        async def embed_candidates(self, texts):
            return EmbeddingResult(((1,),))
    index = SemanticIndex(Broken())
    with pytest.raises(ValueError):
        await index.warm([candidate()])
    assert not index._cache


def test_query_is_independent_of_lexical_hints_and_uses_only_visible_and_anchor():
    req = request("호시노랑 친했어?", anchor_text="호시노랑 친했어", entities=("character.hina",))
    polluted = replace(req, retrieval_text="직접 만남 대면 대화 관계 접점 무기 이름")
    assert semantic_query(polluted) == "호시노랑 친했어?"
    assert semantic_query(replace(polluted, retrieval_text="")) == "호시노랑 친했어?"
    assert semantic_query(replace(polluted, visible_text="정말?")) == "호시노랑 친했어\n정말?"
    assert semantic_query(replace(req, visible_text="")) == ""
    assert semantic_query(req, intent_hint=True).startswith("fact:")


@pytest.mark.parametrize("visible,anchor", [
    ("정말 Hoshino랑, 처음 만난 거야?", "hoshino랑 처음 만난 거야"),
    ("호시노랑 처음 만난 거야?", "호시노랑  처음\n만난 거야"),
])
def test_anchor_containment_ignores_formatting(visible, anchor):
    assert semantic_query(request(visible, anchor_text=anchor)) == visible


async def test_empty_lexical_query_cannot_disable_semantic_recall():
    engine, _ = retriever()
    result = await engine.retrieve(replace(request(), retrieval_text=""),
                                   [candidate()], budget=BUDGET)
    assert ids(result) == ["positive"]


@pytest.mark.parametrize("reject,strong", [(0.5, 0.5), (1, 2), (float('nan'), 1)])
def test_invalid_calibration(reject, strong):
    with pytest.raises(ValueError):
        SemanticCalibration("fake", reject, strong)


def test_calibration_is_piecewise_not_raw_cosine():
    calibration = SemanticCalibration("fake", .5, .9)
    assert calibration.score(.4) == 0
    assert calibration.score(.7) == pytest.approx(.5)
    assert calibration.score(.95) == 1
