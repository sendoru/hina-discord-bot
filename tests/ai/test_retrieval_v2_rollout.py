import asyncio
import json
from types import SimpleNamespace

from hina_bot.ai.freshness import FreshnessMode
from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.information_plan import InformationPlan
from hina_bot.ai.information_routing import InformationRoute
from hina_bot.ai.retrieval_v2_rollout import PreparedRetrievalV2, RetrievalV2Coordinator
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.ai.rp_output_policy import provenance_mode
from hina_bot.core.evidence_sufficiency import EvidenceAssessment
from hina_bot.core.knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
)
from hina_bot.core.retrieval_v2 import KnowledgeBundle, RetrievalIntent, RetrievalRequest
from hina_bot.core.retrieval_v2_runtime import RetrievalV2Run


class UsageCapture:
    def __init__(self):
        self.events = []

    def routing_event(self, operation, **metadata):
        self.events.append((operation, metadata))


def candidate(identifier="canon.secret-row"):
    return KnowledgeCandidate(
        candidate_id=identifier,
        source="static_lore",
        kind="world_fact",
        content="content must never enter telemetry",
        search_text="search text must never enter telemetry",
        subjects=(),
        keywords=(),
        reference=identifier,
        lane="canon",
        usages=(KnowledgeUsage.FACTUAL,),
        fact_type="fact_direct",
        confidence="verified",
        kr_release="confirmed",
        source_metadata=(("type", "official_game"),),
    )


def run_result(*, evidence=None, identifier="canon.secret-row"):
    row = RankedKnowledgeCandidate(1.0, 0, candidate(identifier))
    return RetrievalV2Run(
        bundle=KnowledgeBundle(facts=(row,)),
        evidence=evidence or EvidenceAssessment(
            True,
            "trusted_direct_fact",
            (identifier,),
            answer_state="supported",
        ),
        status="completed",
        calibration_status="configured",
        factual_invocation="semantic_enabled",
        factual_semantic_status="available",
        ambient_semantic_status="not_applicable",
        candidate_factual=10,
        candidate_relation=2,
        candidate_ambient=5,
        candidate_reaction=0,
        selected_factual=1,
        selected_relation=0,
        selected_ambient=0,
        selected_reaction=0,
        factual_lexical_selected=0,
        factual_semantic_only_selected=1,
        semantic_cache_hits=8,
        semantic_cache_misses=2,
        embedding_prompt_tokens=14,
        embedding_requests=1,
        composer_dedup_candidates=0,
        elapsed_ms=12.5,
    )


def information(route=InformationRoute.GENERAL, text="hello"):
    return InformationPlan(
        routing=RoutingPlan(text, text),
        route=route,
        references=({"reference": "legacy.secret", "kind": "world_fact", "content": "legacy"},),
        freshness=FreshnessMode.STATIC,
        fact_question=False,
        search_mode="none",
        provenance=provenance_mode(text, web_search=False),
    )


def prepared():
    return PreparedRetrievalV2(
        request=RetrievalRequest("secret question", "secret query", intent=RetrievalIntent.FACT),
        candidates=(candidate(),),
        scene=SimpleNamespace(),
        legacy_ids=("legacy.secret-id",),
    )


def test_rollout_telemetry_hashes_candidate_ids_and_never_serializes_content():
    coordinator = object.__new__(RetrievalV2Coordinator)
    payload = coordinator.telemetry(
        prepared(),
        run_result(identifier="v2.secret-id"),
        mode="shadow",
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["retrieval_v2_legacy_selected"] == 1
    assert payload["retrieval_v2_selected"] == 1
    assert len(payload["retrieval_v2_legacy_ids"][0]) == 12
    assert len(payload["retrieval_v2_selected_ids"][0]) == 12
    for secret in (
        "legacy.secret-id",
        "v2.secret-id",
        "secret question",
        "secret query",
        "content must never enter telemetry",
    ):
        assert secret not in serialized


async def test_active_timeout_falls_back_to_original_legacy_plan():
    class TimeoutCoordinator:
        async def run_with_timeout(self, _prepared):
            raise TimeoutError

    pipeline = object.__new__(InformationPipeline)
    pipeline.retrieval_v2 = TimeoutCoordinator()
    pipeline.usage = UsageCapture()
    original = information()

    result = await pipeline._apply_active_retrieval_v2(original, prepared())

    assert result is original
    operation, event = pipeline.usage.events[-1]
    assert operation == "retrieval_v2.active"
    assert event["status"] == "timeout"
    assert event["retrieval_v2_fallback_reason"] == "active_timeout_legacy_fallback"


async def test_active_success_replaces_answer_context_with_typed_bundle():
    result_run = run_result()

    class SuccessCoordinator:
        async def run_with_timeout(self, _prepared):
            return result_run

        def telemetry(self, *_args, **_kwargs):
            return {"status": "completed", "retrieval_v2_mode": "active"}

    pipeline = object.__new__(InformationPipeline)
    pipeline.retrieval_v2 = SuccessCoordinator()
    pipeline.usage = UsageCapture()
    pipeline.settings = SimpleNamespace(
        chat_web_search=True,
        runtime_default_location="",
        call_prefixes=("히나야",),
    )

    result = await pipeline._apply_active_retrieval_v2(information(), prepared())

    assert result.retrieval_source == "v2_active"
    assert result.knowledge_bundle is result_run.bundle
    assert result.references == tuple(result_run.bundle.context_sections()["facts"])
    assert pipeline.usage.events[-1][0] == "retrieval_v2.active"


async def test_active_local_then_web_uses_v2_sufficiency_assessment():
    evidence = EvidenceAssessment(
        True,
        "matched_direct_claim",
        ("canon.awareness",),
        "awareness",
        "supported",
    )
    result_run = run_result(evidence=evidence, identifier="canon.awareness")

    class SuccessCoordinator:
        async def run_with_timeout(self, _prepared):
            return result_run

        def telemetry(self, *_args, **_kwargs):
            return {"status": "completed", "retrieval_v2_mode": "active"}

    text = "히나는 호시노를 만나기 전부터 알고 있었어?"
    pipeline = object.__new__(InformationPipeline)
    pipeline.retrieval_v2 = SuccessCoordinator()
    pipeline.usage = UsageCapture()
    pipeline.settings = SimpleNamespace(
        chat_web_search=True,
        runtime_default_location="",
        call_prefixes=("히나야",),
    )

    result = await pipeline._apply_active_retrieval_v2(
        information(InformationRoute.LOCAL_THEN_WEB, text),
        prepared(),
    )

    assert result.search_mode == "none"
    assert result.search_reason == "local_evidence_sufficient"
    assert result.search_decision_source == "retrieval_v2"


async def test_shadow_starts_outside_answer_path_and_busy_state_does_not_queue():
    started = asyncio.Event()
    release = asyncio.Event()

    class ShadowCoordinator:
        async def run_with_timeout(self, _prepared):
            started.set()
            await release.wait()
            return run_result()

        def telemetry(self, *_args, **_kwargs):
            return {"status": "completed", "retrieval_v2_mode": "shadow"}

    pipeline = object.__new__(InformationPipeline)
    pipeline.retrieval_v2 = ShadowCoordinator()
    pipeline.usage = UsageCapture()
    pipeline._retrieval_v2_shadow_tasks = set()

    pipeline._start_retrieval_v2_shadow(prepared())
    await started.wait()
    assert len(pipeline._retrieval_v2_shadow_tasks) == 1

    pipeline._start_retrieval_v2_shadow(prepared())
    assert pipeline.usage.events[-1][1]["retrieval_v2_fallback_reason"] == "shadow_busy"
    assert len(pipeline._retrieval_v2_shadow_tasks) == 1

    release.set()
    await asyncio.gather(*tuple(pipeline._retrieval_v2_shadow_tasks))
    await asyncio.sleep(0)
    assert not pipeline._retrieval_v2_shadow_tasks
