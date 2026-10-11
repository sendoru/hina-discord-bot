import asyncio
from dataclasses import replace
from math import sqrt

import pytest

from hina_bot.ai.retrieval_request import build_resolved_retrieval_request
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.hybrid_retrieval import (
    HybridConfig,
    HybridRetriever,
    rank_factual,
)
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate, rank_lexical_candidates
from hina_bot.core.lore import LoreIndex
from hina_bot.core.profile_retrieval import rank_profile
from hina_bot.core.retrieval_v2 import RetrievalIntent, RetrievalRequest
from hina_bot.core.semantic_retrieval import (
    EmbeddingResult,
    EmbeddingUsage,
    SemanticCalibration,
    SemanticHit,
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
        self.vectors = {
            "positive": (1, 0),
            "negative": (0.4, sqrt(0.84)),
            "unrelated": (0, 1),
        }

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        if self.failure:
            raise RuntimeError("sensitive backend response")
        return EmbeddingResult(
            tuple(self.vectors[text] for text in texts),
            EmbeddingUsage(37, 1),
        )

    async def embed_query(self, text):
        self.queries.append(text)
        if self.query_failure:
            raise RuntimeError("query failed")
        return EmbeddingResult(((1, 0),), EmbeddingUsage(11, 1))


def candidate(identifier="positive", **kwargs):
    return replace(KnowledgeCandidate(
        candidate_id=identifier,
        source="static_lore",
        kind="world_fact",
        content=identifier,
        search_text=identifier,
        subjects=(),
        keywords=(),
    ), **kwargs)


def request(text="표현이 완전히 다른 질문", **kwargs):
    return RetrievalRequest(text, text, intent=RetrievalIntent.FACT, **kwargs)


def retriever(backend=None, **kwargs):
    backend = backend or FakeBackend()
    config = HybridConfig(
        calibration=SemanticCalibration(backend.cache_key, 0.5, 0.9),
        **kwargs,
    )
    return HybridRetriever(SemanticIndex(backend), config=config), backend


def ids(result):
    return [row.candidate.candidate_id for row in result.rows]


async def test_runtime_policy_preserves_lexical_then_appends_semantic_only_recall():
    engine, backend = retriever()
    rows = [
        candidate("negative", keywords=("완전히", "질문")),
        candidate(),
    ]
    lexical = rank_lexical_candidates(request().retrieval_text, rows)
    assert lexical[0].candidate == rows[0]

    result = await engine.retrieve(request(), rows)

    assert ids(result) == ["negative", "positive"]
    assert len(backend.queries) == 1
    assert result.semantic_status == "available"


def test_rank_factual_never_lets_semantic_rejection_veto_lexical_admission():
    config = HybridConfig(
        calibration=SemanticCalibration("fake:v1:2", 0.5, 0.9),
    )
    rows = [
        candidate("negative", keywords=("완전히", "질문")),
        candidate(),
    ]
    ranked = rank_factual(
        request(),
        rows,
        config,
        semantic_hits=(
            SemanticHit(0, 0.0),
            SemanticHit(1, 1.0),
        ),
    )
    assert [row.candidate.candidate_id for row in ranked] == ["negative", "positive"]


async def test_semantic_search_scores_entire_factual_corpus():
    class SpyIndex:
        class Backend:
            cache_key = "fake:v1:2"

        backend = Backend()

        def __init__(self):
            self.top_k = None

        async def search(self, query, rows, *, top_k):
            self.top_k = top_k
            return type("Result", (), {
                "hits": tuple(SemanticHit(i, 1.0 - i * 0.01) for i in range(len(rows))),
                "cache_hits": 0,
                "cache_misses": len(rows),
            })()

    rows = [
        candidate(f"row-{i}", semantic_text="positive")
        for i in range(25)
    ]
    index = SpyIndex()
    engine = HybridRetriever(
        index,
        config=HybridConfig(
            calibration=SemanticCalibration("fake:v1:2", 0.5, 0.9),
        ),
    )
    result = await engine.retrieve(request(), rows)

    assert index.top_k == len(rows)
    assert len(result.rows) == len(rows)


async def test_unrelated_factual_query_can_have_zero_results():
    engine, _ = retriever()
    result = await engine.retrieve(request(), [candidate("unrelated")])
    assert not result.rows


async def test_default_without_calibration_makes_no_calls():
    backend = FakeBackend()
    engine = HybridRetriever(SemanticIndex(backend))
    result = await engine.retrieve(
        request("생일"),
        [candidate(keywords=("생일",))],
    )
    assert not backend.queries and not backend.documents
    assert result.semantic_status == "unavailable"
    assert not result.rows


@pytest.mark.parametrize("field", ["생일", "학년", "무기", "소속", "직책"])
async def test_profile_is_separate_exact_lexical_path_and_hybrid_stays_safe(field):
    backend = FakeBackend()
    engine = HybridRetriever(SemanticIndex(backend), config=HybridConfig(
        calibration=SemanticCalibration(backend.cache_key, 0.5, 0.9),
    ))
    req = RetrievalRequest(field, field, intent=RetrievalIntent.PROFILE)
    rows = [
        candidate("negative", keywords=(field,) * 4),
        candidate("positive", keywords=(field,) * 5),
    ]

    profile = rank_profile(req, rows)
    expected = rank_lexical_candidates(field, rows)
    assert [row.candidate.candidate_id for row in profile] == [
        row.candidate.candidate_id for row in expected
    ]

    accidental = await engine.retrieve(req, rows)
    assert not accidental.rows
    assert accidental.semantic_status == "not_applicable"
    assert not backend.queries and not backend.documents


async def test_real_resolved_profile_queries_use_profile_path_with_packaged_corpus():
    rows = LoreIndex.load().candidates()
    for text, expected in (
        ("히나야 생일 언제야?", "canon.hina.birthday"),
        ("히나야 무기 이름 뭐야?", "canon.hina.weapon.name_and_class"),
    ):
        req = build_resolved_retrieval_request(
            RoutingPlan(text, text),
            call_prefixes=("히나야",),
        )
        assert req.intent == RetrievalIntent.PROFILE
        assert expected in [
            row.candidate.candidate_id for row in rank_profile(req, rows)
        ]


async def test_configured_strong_lexical_hit_skips_semantics():
    engine, backend = retriever(skip_semantic_at_lexical=14)
    result = await engine.retrieve(
        request("생일"),
        [candidate(keywords=("생일",) * 2)],
    )
    assert ids(result) == ["positive"]
    assert result.semantic_status == "lexical_short_circuit"
    assert not backend.queries


@pytest.mark.parametrize("stage", ["failure", "query_failure"])
async def test_backend_failure_uses_thresholded_lexical_fallback(stage):
    engine, backend = retriever()
    setattr(backend, stage, True)
    result = await engine.retrieve(
        request("생일"),
        [candidate(keywords=("생일",) * 2)],
    )
    assert ids(result) == ["positive"]
    assert result.semantic_status == "failed"
    assert "sensitive" not in repr(result)


@pytest.mark.parametrize("failure", [False, True])
async def test_factual_retrieval_does_not_interpret_relation_pair_as_candidate_gate(failure):
    engine, backend = retriever()
    backend.failure = failure
    pair = ("character.hina", "character.hoshino")
    req = request("생일", entities=pair, relation_pair=pair)
    rows = [
        candidate(
            "different-relation",
            semantic_text="positive",
            entities=("character.hina", "character.ako"),
            keywords=("생일",) * 10,
        ),
        candidate(
            "unannotated",
            semantic_text="negative",
            keywords=("생일",) * 2,
        ),
        candidate(
            "partial",
            semantic_text="unrelated",
            entities=("character.hina",),
            keywords=("생일",) * 2,
        ),
        candidate(
            entities=pair,
            keywords=("생일",) * 2,
        ),
    ]
    result = await engine.retrieve(req, rows)
    assert {
        "different-relation",
        "unannotated",
        "partial",
        "positive",
    } <= set(ids(result))
    if not failure:
        assert set(backend.documents) == {"positive", "negative", "unrelated"}


async def test_conversation_intent_does_not_block_semantic_retrieval_once_invoked():
    engine, backend = retriever()
    req = replace(request(), intent=RetrievalIntent.CONVERSATION)
    result = await engine.retrieve(req, [candidate()])
    assert ids(result) == ["positive"]
    assert result.semantic_status == "available"
    assert backend.documents == ["positive"]
    assert len(backend.queries) == 1


async def test_non_factual_usage_skips_embedding():
    engine, backend = retriever()
    result = await engine.retrieve(
        request(),
        [candidate(kind="optional_reaction")],
    )
    assert not result.rows
    assert not backend.documents and not backend.queries


async def test_resolved_relation_pair_is_not_a_factual_candidate_filter():
    pair_text = "호시노랑 무슨 사이야?"
    req = build_resolved_retrieval_request(RoutingPlan(pair_text, pair_text))
    assert set(req.relation_pair or ()) == {
        "character.hina",
        "character.hoshino",
    }
    rows = [
        candidate(
            "unannotated",
            semantic_text="positive",
            keywords=("사이",) * 2,
            entities=(),
        ),
        candidate(
            "conflict",
            semantic_text="positive",
            keywords=("사이",) * 2,
            entities=("character.hina", "character.ako"),
        ),
    ]
    engine = HybridRetriever()
    result = await engine.retrieve(req, rows)
    assert ids(result) == ["unannotated", "conflict"]


async def test_actual_builder_classifier_miss_does_not_block_semantic_channel():
    text = "호시노랑 예전부터 친했던 거야?"
    req = build_resolved_retrieval_request(RoutingPlan(text, text))
    assert req.intent == RetrievalIntent.CONVERSATION
    engine, backend = retriever()
    result = await engine.retrieve(req, [candidate()])
    assert ids(result) == ["positive"]
    assert result.semantic_status == "available"
    assert backend.queries == [text]


async def test_sources_are_not_priorities_and_semantic_ties_are_stable():
    engine, _ = retriever()
    rows = [
        candidate("one", source="static_lore", semantic_text="positive"),
        candidate("two", source="runtime_knowledge", semantic_text="positive"),
    ]
    assert ids(await engine.retrieve(request(), rows)) == ["one", "two"]
    assert ids(await engine.retrieve(request(), rows[::-1])) == ["two", "one"]


async def test_current_reference_and_guard_are_kept_when_embedding_is_reused():
    engine, backend = retriever()
    await engine.retrieve(request(), [candidate()])
    changed = candidate(
        kind="interpretation",
        metadata=(("guard", "do_not_assert_positive_fact"),),
    )
    result = await engine.retrieve(request(), [changed])
    assert result.rows[0].candidate == changed
    assert backend.documents == ["positive"]
    assert result.cache_hits == 1 and result.cache_misses == 0


async def test_representation_cache_invalidation_and_removal():
    backend = FakeBackend()
    index = SemanticIndex(backend)
    rows = [
        candidate(subjects=("반복",) * 10, keywords=("반복",) * 10),
        candidate(
            "runtime",
            source="runtime_knowledge",
            semantic_text="negative",
        ),
    ]
    await index.search("question", rows, top_k=3)
    await index.search("another question", rows, top_k=3)
    assert backend.documents == ["positive", "negative"]
    assert len(backend.queries) == 2

    changed = [
        replace(rows[0], semantic_text="unrelated"),
        replace(rows[1], search_text="unrelated", semantic_text=None),
    ]
    result = await index.search("question", changed, top_k=3)
    assert backend.documents == ["positive", "negative", "unrelated"]
    assert result.cache_misses == 1
    assert len((await index.search("question", changed[:1], top_k=3)).hits) == 1

    backend.cache_key = "fake:v2:2"
    await index.search("question", changed[:1], top_k=3)
    assert backend.documents[-2:] == ["unrelated", "unrelated"]


async def test_calibration_model_mismatch_falls_back_without_calls():
    engine, backend = retriever()
    backend.cache_key = "new-model"
    result = await engine.retrieve(
        request("생일"),
        [candidate(keywords=("생일",) * 2)],
    )
    assert result.semantic_status == "calibration_mismatch"
    assert ids(result) == ["positive"]
    assert not backend.queries and not backend.documents


async def test_concurrent_fill_and_bounded_cache():
    backend = FakeBackend()
    index = SemanticIndex(backend, max_entries=1)
    await asyncio.gather(
        *(index.search("q", [candidate()], top_k=1) for _ in range(3))
    )
    assert backend.documents == ["positive"]
    assert len(backend.queries) == 3

    await index.warm([candidate("negative")])
    await index.warm([candidate()])
    assert backend.documents == ["positive", "negative", "positive"]
    assert len(backend.queries) == 3


async def test_matrix_larger_than_cache_remains_complete():
    index = SemanticIndex(FakeBackend(), max_entries=1)
    result = await index.search(
        "q",
        [candidate(), candidate("negative")],
        top_k=2,
    )
    assert len(result.hits) == 2
    assert result.hits[0].order == 0


async def test_timeout_and_cancellation():
    class Slow(FakeBackend):
        async def embed_query(self, text):
            await asyncio.Event().wait()

    engine, _ = retriever(Slow(), timeout_seconds=0.01)
    result = await engine.retrieve(
        request("생일"),
        [candidate(keywords=("생일",) * 2)],
    )
    assert result.semantic_status == "failed"
    assert ids(result) == ["positive"]

    task = asyncio.create_task(engine.retrieve(request(), [candidate()]))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "vector,dimensions",
    [
        ([], 2),
        ([1], 2),
        ([0, 0], 2),
        ([float("nan"), 0], 2),
        ([float("inf"), 1], 2),
    ],
)
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
    req = request(
        "호시노랑 친했어?",
        anchor_text="호시노랑 친했어",
        entities=("character.hina",),
    )
    polluted = replace(
        req,
        retrieval_text="직접 만남 대면 대화 관계 접점 무기 이름",
    )
    assert semantic_query(polluted) == "호시노랑 친했어?"
    assert semantic_query(replace(polluted, retrieval_text="")) == "호시노랑 친했어?"
    assert semantic_query(replace(polluted, visible_text="정말?")) == "호시노랑 친했어\n정말?"
    assert semantic_query(replace(req, visible_text="")) == ""


@pytest.mark.parametrize(
    "visible,anchor",
    [
        ("정말 Hoshino랑, 처음 만난 거야?", "hoshino랑 처음 만난 거야"),
        ("호시노랑 처음 만난 거야?", "호시노랑  처음\n만난 거야"),
    ],
)
def test_anchor_containment_ignores_formatting(visible, anchor):
    assert semantic_query(request(visible, anchor_text=anchor)) == visible


async def test_empty_lexical_query_cannot_disable_semantic_recall():
    engine, _ = retriever()
    result = await engine.retrieve(
        replace(request(), retrieval_text=""),
        [candidate()],
    )
    assert ids(result) == ["positive"]


@pytest.mark.parametrize(
    "reject,strong",
    [(0.5, 0.5), (1, 2), (float("nan"), 1)],
)
def test_invalid_calibration(reject, strong):
    with pytest.raises(ValueError):
        SemanticCalibration("fake", reject, strong)


def test_calibration_is_piecewise_not_raw_cosine():
    calibration = SemanticCalibration("fake", 0.5, 0.9)
    assert calibration.score(0.4) == 0
    assert calibration.score(0.7) == pytest.approx(0.5)
    assert calibration.score(0.95) == 1
