from types import SimpleNamespace

import pytest

from hina_bot.ai.retrieval_request import build_resolved_retrieval_request
from hina_bot.ai.retrieval_v2_rollout import RetrievalV2Controller
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.ambient_retrieval import AmbientSceneContext
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate, KnowledgeUsage
from hina_bot.core.lore import LoreIndex
from hina_bot.core.retrieval_v2 import RetrievalIntent, RetrievalRequest
from hina_bot.core.retrieval_v2_runtime import (
    RetrievalV2Budgets,
    RetrievalV2Engine,
    comparison_metrics,
)
from hina_bot.core.semantic_retrieval import (
    EmbeddingResult,
    EmbeddingUsage,
    SemanticCalibration,
    SemanticIndex,
)


async def test_end_to_end_profile_and_relation_composition_without_semantic_backend():
    lore = LoreIndex.load()
    engine = RetrievalV2Engine(lore)

    profile_request = build_resolved_retrieval_request(
        RoutingPlan("히나야 생일 언제야?", "히나야 생일 언제야?"),
        call_prefixes=("히나야",),
    )
    profile = await engine.retrieve(profile_request)
    assert "canon.hina.birthday" in profile.selected_ids
    assert profile.factual_status == "profile_exact"
    assert profile.ambient_status == "not_invoked"

    relation_request = build_resolved_retrieval_request(
        RoutingPlan("호시노는 선배야?", "호시노는 선배야?"),
    )
    relation = await engine.retrieve(relation_request)
    assert relation.bundle.relations
    assert relation.relation_candidates >= len(relation.bundle.relations)
    assert relation.evidence.predicate == "school_year_relation"
    assert relation.evidence.sufficient


async def test_conversation_without_calibration_stays_local_and_does_not_invent_ambient():
    engine = RetrievalV2Engine(LoreIndex.load())
    request = build_resolved_retrieval_request(
        RoutingPlan("오늘 좀 피곤하네", "오늘 좀 피곤하네"),
    )
    result = await engine.retrieve(request)
    assert result.factual_invocation == "conversation_semantic"
    assert result.factual_status == "unavailable"
    assert result.ambient_status == "not_invoked"
    assert not result.bundle.character_insights


def test_comparison_metrics_are_content_free_and_hash_ids():
    engine = RetrievalV2Engine(LoreIndex.load())

    async def build():
        request = build_resolved_retrieval_request(
            RoutingPlan("히나야 생일 언제야?", "히나야 생일 언제야?"),
            call_prefixes=("히나야",),
        )
        return await engine.retrieve(request)

    result = __import__("asyncio").run(build())
    metrics = comparison_metrics(
        [{"reference": "canon.hina.birthday", "content": "sensitive raw content"}],
        result,
    )
    assert metrics["retrieval_v2_legacy_selected"] == 1
    assert metrics["retrieval_v2_legacy_ids"]
    rendered = repr(metrics)
    assert "sensitive raw content" not in rendered
    assert "canon.hina.birthday" not in rendered


def _settings(**overrides):
    values = {
        "gemini_api_key": "",
        "retrieval_v2_embedding_dimensions": 768,
        "retrieval_v2_timeout_seconds": 3.0,
        "retrieval_v2_calibration_backend_key": "",
        "retrieval_v2_factual_reject": None,
        "retrieval_v2_factual_strong": None,
        "retrieval_v2_ambient_reject": None,
        "retrieval_v2_ambient_strong": None,
        "lore_max_items": 6,
        "lore_max_chars": 3200,
        "community_lore": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({}, "gemini_key_missing"),
        ({"gemini_api_key": "key"}, "calibration_backend_key_missing"),
        ({
            "gemini_api_key": "key",
            "retrieval_v2_calibration_backend_key": "wrong",
        }, "calibration_backend_key_mismatch"),
        ({
            "gemini_api_key": "key",
            "retrieval_v2_calibration_backend_key": (
                "gemini:gemini-embedding-2:1:768:search-document-v1"
            ),
        }, "calibration_thresholds_missing"),
    ],
)
async def test_rollout_controller_fails_closed_until_calibration_matches(overrides, reason):
    controller = RetrievalV2Controller(_settings(**overrides), LoreIndex.load())
    try:
        assert not controller.active_ready
        assert controller.semantic_gate_reason == reason
    finally:
        await controller.close()


async def test_rollout_controller_active_gate_accepts_matching_calibration_identity():
    controller = RetrievalV2Controller(
        _settings(
            gemini_api_key="key",
            retrieval_v2_calibration_backend_key=(
                "gemini:gemini-embedding-2:1:768:search-document-v1"
            ),
            retrieval_v2_factual_reject=0.4,
            retrieval_v2_factual_strong=0.8,
            retrieval_v2_ambient_reject=0.5,
            retrieval_v2_ambient_strong=0.9,
        ),
        LoreIndex.load(),
    )
    try:
        assert controller.active_ready
        assert controller.semantic_gate_reason == "ready"
    finally:
        await controller.close()

class _FakeEmbedding:
    cache_key = "fake:rollout:v1:2"
    dimensions = 2

    def __init__(self):
        self.documents = []
        self.queries = []

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        return EmbeddingResult(
            tuple((1.0, 0.0) for _ in texts),
            EmbeddingUsage(len(texts) * 3, 1),
        )

    async def embed_query(self, text):
        self.queries.append(text)
        return EmbeddingResult(((1.0, 0.0),), EmbeddingUsage(5, 1))


def _reaction_candidate():
    return KnowledgeCandidate(
        candidate_id="community.test.reaction",
        source="runtime_knowledge",
        kind="optional_reaction",
        content="머리 크기 놀림에 반응",
        search_text="머리 크기 놀림",
        subjects=("머리",),
        keywords=("머리", "크기", "놀림"),
        usages=(KnowledgeUsage.REACTION,),
        lane="community_meme",
    )


async def test_conversation_classifier_miss_invokes_semantic_only():
    backend = _FakeEmbedding()
    calibration = SemanticCalibration(backend.cache_key, 0.5, 0.9)
    engine = RetrievalV2Engine(
        LoreIndex.load(),
        semantic_index=SemanticIndex(backend),
        factual_calibration=calibration,
    )
    text = "쟤 원래 저렇게까지 일을 놓질 못해?"
    request = build_resolved_retrieval_request(RoutingPlan(text, text))
    assert request.intent == RetrievalIntent.CONVERSATION
    result = await engine.retrieve(request)
    assert result.factual_invocation == "conversation_semantic"
    assert result.factual_status == "available"
    assert result.semantic_selected > 0
    assert result.lexical_selected == 0
    assert len(backend.queries) == 1

    offline = await RetrievalV2Engine(LoreIndex.load()).retrieve(request)
    assert offline.factual_status == "unavailable"
    assert not offline.bundle.facts


async def test_trivial_turn_skips_factual_embedding():
    backend = _FakeEmbedding()
    calibration = SemanticCalibration(backend.cache_key, 0.5, 0.9)
    engine = RetrievalV2Engine(
        LoreIndex.load(), semantic_index=SemanticIndex(backend),
        factual_calibration=calibration,
    )
    result = await engine.retrieve(
        build_resolved_retrieval_request(RoutingPlan("응", "응"))
    )
    assert result.factual_invocation == "not_needed"
    assert not backend.queries


async def test_end_to_end_ambient_and_reaction_lanes_share_total_budget():
    backend = _FakeEmbedding()
    calibration = SemanticCalibration(backend.cache_key, 0.5, 0.9)
    engine = RetrievalV2Engine(
        LoreIndex.load(),
        semantic_index=SemanticIndex(backend),
        factual_calibration=calibration,
        ambient_calibration=calibration,
        budgets=RetrievalV2Budgets(
            max_items=3,
            max_chars=3200,
            relation_items=2,
            ambient_items=2,
            ambient_chars=900,
            reaction_items=1,
        ),
    )
    request = RetrievalRequest(
        "머리 크기 놀리지 말고 오늘은 좀 쉬어.",
        "머리 크기 놀림",
        intent=RetrievalIntent.CONVERSATION,
    )
    scene = AmbientSceneContext(
        "character.hina",
        recent_same_speaker=("오늘은 일 그만해.",),
        relationship_signal="편안함 3/4",
    )

    result = await engine.retrieve(
        request,
        runtime_candidates=(_reaction_candidate(),),
        scene=scene,
    )

    assert result.factual_invocation == "conversation_semantic"
    assert result.factual_status == "available"
    assert result.ambient_status == "available"
    assert 1 <= len(result.bundle.character_insights) <= 2
    assert [row.candidate.candidate_id for row in result.bundle.reactions] == [
        "community.test.reaction"
    ]
    assert len(result.selected_ids) <= 3
    assert result.embedding_requests >= 1

