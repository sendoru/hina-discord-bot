from dataclasses import replace
from math import sqrt

import pytest

from hina_bot.core.ambient_retrieval import AmbientSceneContext
from hina_bot.core.evidence_sufficiency import EvidenceAssessment
from hina_bot.core.hybrid_retrieval import HybridConfig
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate, KnowledgeUsage
from hina_bot.core.relationship_grounding import RelationshipGrounder
from hina_bot.core.retrieval_v2 import RetrievalIntent, RetrievalRequest
from hina_bot.core.retrieval_v2_runtime import RetrievalV2Budgets, retrieve_v2
from hina_bot.core.semantic_retrieval import (
    EmbeddingResult,
    EmbeddingUsage,
    SemanticCalibration,
    SemanticIndex,
)


class FakeBackend:
    cache_key = "fake:retrieval-v2:v1:2"
    dimensions = 2

    def __init__(self):
        self.documents = []
        self.queries = []
        self.vectors = {
            "factual-semantic": (1.0, 0.0),
            "ambient-rest": (1.0, 0.0),
            "unrelated": (0.0, 1.0),
        }

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        return EmbeddingResult(
            tuple(self.vectors.get(text, (0.0, 1.0)) for text in texts),
            EmbeddingUsage(30, 1),
        )

    async def embed_query(self, text):
        self.queries.append(text)
        return EmbeddingResult(((1.0, 0.0),), EmbeddingUsage(10, 1))


def candidate(identifier, *, usages=(KnowledgeUsage.FACTUAL,), **overrides):
    base = KnowledgeCandidate(
        candidate_id=identifier,
        source="static_lore",
        kind="world_fact",
        content=identifier,
        search_text=identifier,
        subjects=(),
        keywords=(),
        reference=identifier,
        lane="canon",
        usages=usages,
        confidence="verified",
        kr_release="confirmed",
        source_metadata=(("type", "official_game"),),
        fact_type="fact_direct",
    )
    return replace(base, **overrides)


def budgets():
    return RetrievalV2Budgets.from_legacy(6, 3200)


async def test_profile_uses_exact_lexical_path_without_semantic_calls():
    backend = FakeBackend()
    index = SemanticIndex(backend)
    calibration = SemanticCalibration(backend.cache_key, 0.4, 0.8)
    row = candidate(
        "birthday",
        keywords=("생일",),
        entities=("character.hina",),
    )
    request = RetrievalRequest(
        "생일 언제야?",
        "생일",
        entities=("character.hina",),
        intent=RetrievalIntent.PROFILE,
    )
    run = await retrieve_v2(
        request,
        [row],
        grounder=RelationshipGrounder([]),
        semantic_index=index,
        calibration=calibration,
        calibration_status="configured",
        scene=AmbientSceneContext("character.hina"),
        budgets=budgets(),
    )

    assert [item.candidate.candidate_id for item in run.bundle.facts] == ["birthday"]
    assert run.factual_invocation == "profile_exact"
    assert run.factual_semantic_status == "not_applicable"
    assert not backend.queries and not backend.documents


async def test_conversation_without_factual_signal_skips_factual_semantics_but_can_use_ambient():
    backend = FakeBackend()
    index = SemanticIndex(backend)
    calibration = SemanticCalibration(backend.cache_key, 0.4, 0.8)
    ambient = candidate(
        "ambient",
        usages=(KnowledgeUsage.FACTUAL, KnowledgeUsage.AMBIENT),
        kind="interpretation",
        fact_type="inference",
        awareness="inference",
        entities=("character.hina",),
        semantic_text="ambient-rest",
        evidence_ids=("support",),
    )
    request = RetrievalRequest(
        "오늘은 좀 쉬자.",
        "오늘은 좀 쉬자.",
        intent=RetrievalIntent.CONVERSATION,
    )
    run = await retrieve_v2(
        request,
        [ambient],
        grounder=RelationshipGrounder([]),
        semantic_index=index,
        calibration=calibration,
        calibration_status="configured",
        scene=AmbientSceneContext("character.hina"),
        budgets=budgets(),
    )

    assert run.factual_invocation == "conversation_no_factual_signal"
    assert run.factual_semantic_status == "unavailable"
    assert [item.candidate.candidate_id for item in run.bundle.character_insights] == [
        "ambient"
    ]
    assert run.selected_ambient == 1
    assert run.embedding_requests >= 1


async def test_disabled_calibration_keeps_v2_lexical_and_exact_lanes_without_embedding():
    backend = FakeBackend()
    index = SemanticIndex(backend)
    pair = ("character.hina", "character.hoshino")
    relation = candidate(
        "relation",
        usages=(KnowledgeUsage.RELATION,),
        entities=pair,
    )
    factual = candidate("factual", keywords=("사건",))
    request = RetrievalRequest(
        "호시노 사건 알려줘",
        "호시노 사건",
        entities=pair,
        relation_pair=pair,
        intent=RetrievalIntent.RELATIONSHIP_OR_EVENT,
    )
    run = await retrieve_v2(
        request,
        [relation, factual],
        grounder=RelationshipGrounder([relation]),
        semantic_index=index,
        calibration=None,
        calibration_status="disabled_unmeasured",
        scene=AmbientSceneContext("character.hina"),
        budgets=budgets(),
    )

    assert [item.candidate.candidate_id for item in run.bundle.relations] == ["relation"]
    assert [item.candidate.candidate_id for item in run.bundle.facts] == ["factual"]
    assert run.factual_invocation == "calibration_disabled"
    assert run.embedding_requests == 0
    assert not backend.queries and not backend.documents


async def test_semantic_factual_recall_and_usage_are_visible_in_run_diagnostics():
    backend = FakeBackend()
    index = SemanticIndex(backend)
    calibration = SemanticCalibration(backend.cache_key, 0.4, 0.8)
    semantic = candidate(
        "semantic",
        semantic_text="factual-semantic",
        entities=("character.hina",),
    )
    unrelated = candidate(
        "other",
        semantic_text="unrelated",
        entities=("character.hina",),
    )
    request = RetrievalRequest(
        "표현이 다른 사실 질문",
        "",
        entities=("character.hina",),
        intent=RetrievalIntent.FACT,
    )
    run = await retrieve_v2(
        request,
        [semantic, unrelated],
        grounder=RelationshipGrounder([]),
        semantic_index=index,
        calibration=calibration,
        calibration_status="configured",
        scene=AmbientSceneContext("character.hina"),
        budgets=budgets(),
    )

    assert [item.candidate.candidate_id for item in run.bundle.facts] == ["semantic"]
    assert run.factual_semantic_only_selected == 1
    assert run.factual_lexical_selected == 0
    assert run.embedding_prompt_tokens == 40
    assert run.embedding_requests == 2
    assert run.semantic_cache_misses == 2


async def test_bundle_composition_deduplicates_multi_usage_relation_before_factual():
    pair = ("character.hina", "character.hoshino")
    shared = candidate(
        "shared",
        usages=(KnowledgeUsage.FACTUAL, KnowledgeUsage.RELATION),
        entities=pair,
        keywords=("관계",) * 3,
    )
    request = RetrievalRequest(
        "호시노 관계",
        "호시노 관계",
        entities=pair,
        relation_pair=pair,
        intent=RetrievalIntent.RELATIONSHIP_OR_EVENT,
    )
    run = await retrieve_v2(
        request,
        [shared],
        grounder=RelationshipGrounder([shared]),
        semantic_index=None,
        calibration=None,
        calibration_status="embedding_backend_unavailable",
        scene=AmbientSceneContext("character.hina"),
        budgets=budgets(),
    )

    assert [item.candidate.candidate_id for item in run.bundle.relations] == ["shared"]
    assert not run.bundle.facts
    assert run.composer_dedup_candidates == 1


def test_budget_defaults_preserve_total_legacy_context_cap():
    value = RetrievalV2Budgets.from_legacy(6, 3200)
    assert value.factual.max_items == 6
    assert value.relation.max_items == 4
    assert value.ambient.max_items == 2
    assert value.reaction.max_items == 1
    assert value.max_total_chars == 3200
