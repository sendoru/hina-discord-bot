import asyncio
import json
from types import MethodType, SimpleNamespace

from hina_bot.ai.freshness import FreshnessMode
from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.information_plan import InformationPlan
from hina_bot.ai.information_routing import InformationRoute
from hina_bot.ai.retrieval_v2_rollout import RetrievalV2Controller
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.ai.rp_output_policy import ProvenanceMode
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.evidence_sufficiency import EvidenceAssessment
from hina_bot.core.knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
)
from hina_bot.core.lore import LoreIndex
from hina_bot.core.retrieval_v2 import KnowledgeBundle, RetrievalRequest
from hina_bot.core.retrieval_v2_runtime import RetrievalV2Result

HINA = "character.hina"
HOSHINO = "character.hoshino"


def candidate(identifier, usage):
    return KnowledgeCandidate(
        candidate_id=identifier,
        source="static_lore",
        reference=identifier,
        kind=("interpretation" if usage == KnowledgeUsage.AMBIENT else "world_fact"),
        content="context",
        search_text="context",
        subjects=(),
        keywords=(),
        lane="canon",
        usages=(usage,),
        entities=((HINA, HOSHINO) if usage == KnowledgeUsage.RELATION else (HINA,)),
        fact_type=("inference" if usage == KnowledgeUsage.AMBIENT else "fact_direct"),
        awareness=("inference" if usage == KnowledgeUsage.AMBIENT else "direct_experience"),
        confidence="crosschecked",
        kr_release="confirmed",
        source_metadata=(("type", "curated"),),
    )


def ranked(identifier, usage, order=0):
    return RankedKnowledgeCandidate(1.0, order, candidate(identifier, usage))


def result(bundle, *, sufficient=True):
    return RetrievalV2Result(
        request=RetrievalRequest("질문", "질문", entities=(HINA,)),
        bundle=bundle,
        evidence=EvidenceAssessment(
            sufficient,
            "matched_direct_claim" if sufficient else "proposition_not_covered",
        ),
        factual_status="available",
        ambient_status="available",
    )


def information():
    return InformationPlan(
        routing=RoutingPlan("질문", "질문"),
        route=InformationRoute.LOCAL_THEN_WEB,
        references=({"reference": "canon.legacy", "kind": "world_fact", "content": "legacy"},),
        freshness=FreshnessMode.STATIC,
        fact_question=True,
        search_mode="required",
        provenance=ProvenanceMode.NATURAL_LOOKUP,
        search_baseline_mode="required",
        search_locked=True,
        search_reason="legacy_missing",
    )


async def test_shadow_start_does_not_wait_for_retrieval_completion():
    pipeline = object.__new__(InformationPipeline)
    pipeline._retrieval_shadow_tasks = set()
    started = asyncio.Event()
    release = asyncio.Event()

    async def observe(self, *_args, **_kwargs):
        started.set()
        await release.wait()

    pipeline._observe_retrieval_v2 = MethodType(observe, pipeline)
    pipeline._emit_retrieval_v2 = lambda *_args, **_kwargs: None

    pipeline._start_retrieval_v2_shadow(
        RoutingPlan("안녕", "안녕"),
        (),
        store=object(),
        scope=SimpleNamespace(user_id=1),
        channel_rows=(),
        use_memory=True,
    )

    await asyncio.wait_for(started.wait(), 0.2)
    assert len(pipeline._retrieval_shadow_tasks) == 1
    assert not next(iter(pipeline._retrieval_shadow_tasks)).done()

    release.set()
    await asyncio.gather(*tuple(pipeline._retrieval_shadow_tasks))
    await asyncio.sleep(0)
    assert not pipeline._retrieval_shadow_tasks


async def test_shadow_backpressure_skips_new_work_without_waiting():
    pipeline = object.__new__(InformationPipeline)
    blocker = asyncio.Event()
    first = asyncio.create_task(blocker.wait())
    second = asyncio.create_task(blocker.wait())
    pipeline._retrieval_shadow_tasks = {first, second}
    events = []
    pipeline._emit_retrieval_v2 = lambda *args, **kwargs: events.append((args, kwargs))

    pipeline._start_retrieval_v2_shadow(
        RoutingPlan("안녕", "안녕"),
        (),
        store=object(),
        scope=SimpleNamespace(user_id=1),
        channel_rows=(),
        use_memory=True,
    )

    assert len(pipeline._retrieval_shadow_tasks) == 2
    assert events[-1][0][0:2] == ("shadow", "skipped_backpressure")
    assert events[-1][1]["fallback_reason"] == "backpressure"
    blocker.set()
    await asyncio.gather(first, second)


def test_active_plan_separates_fact_relation_references_from_ambient_reaction_bundle():
    pipeline = object.__new__(InformationPipeline)
    pipeline.settings = SimpleNamespace(chat_web_search=True)
    bundle = KnowledgeBundle(
        relations=(ranked("canon.relation", KnowledgeUsage.RELATION),),
        facts=(ranked("canon.fact", KnowledgeUsage.FACTUAL),),
        character_insights=(ranked("canon.insight", KnowledgeUsage.AMBIENT),),
        reactions=(ranked("meme.reaction", KnowledgeUsage.REACTION),),
    )

    activated = pipeline._activate_retrieval_v2(information(), result(bundle))

    assert activated.retrieval_v2_applied is True
    assert activated.retrieval_v2_bundle is bundle
    assert [row["reference"] for row in activated.references] == [
        "canon.relation",
        "canon.fact",
    ]
    assert [row["retrieval_usage"] for row in activated.references] == [
        "relation",
        "factual",
    ]
    assert activated.search_mode == "none"
    assert activated.search_reason == "matched_direct_claim"
    assert activated.search_decision_source == "retrieval_v2"
    assert activated.provenance == ProvenanceMode.SILENT


def test_active_plan_requires_web_when_structured_v2_evidence_is_insufficient():
    pipeline = object.__new__(InformationPipeline)
    pipeline.settings = SimpleNamespace(chat_web_search=True)
    bundle = KnowledgeBundle(facts=(ranked("canon.fact", KnowledgeUsage.FACTUAL),))

    activated = pipeline._activate_retrieval_v2(
        information(),
        result(bundle, sufficient=False),
    )

    assert activated.search_mode == "required"
    assert activated.search_reason == "proposition_not_covered"
    assert activated.provenance == ProvenanceMode.NATURAL_LOOKUP


async def test_controller_overall_timeout_returns_per_turn_legacy_fallback():
    settings = SimpleNamespace(
        gemini_api_key="",
        retrieval_v2_embedding_dimensions=768,
        retrieval_v2_timeout_seconds=0.01,
        retrieval_v2_calibration_backend_key="",
        retrieval_v2_factual_reject=None,
        retrieval_v2_factual_strong=None,
        retrieval_v2_ambient_reject=None,
        retrieval_v2_ambient_strong=None,
        lore_max_items=6,
        lore_max_chars=3200,
        community_lore=True,
    )
    controller = RetrievalV2Controller(settings, LoreIndex.load())

    class SlowEngine:
        async def retrieve(self, *_args, **_kwargs):
            await asyncio.sleep(1)

    controller.engine = SlowEngine()
    try:
        run = await controller.retrieve(RetrievalRequest("x", "x"))
        assert run.result is None
        assert run.status == "timeout"
        assert run.fallback_reason == "overall_timeout"
    finally:
        await controller.close()


def test_rollout_telemetry_has_gate_and_no_reference_content():
    pipeline = object.__new__(InformationPipeline)
    events = []
    pipeline.usage = SimpleNamespace(
        routing_event=lambda operation, **fields: events.append((operation, fields))
    )
    pipeline.retrieval_v2 = SimpleNamespace(
        semantic_gate_reason="calibration_thresholds_missing",
    )

    pipeline._emit_retrieval_v2(
        "shadow",
        "gated",
        [{"reference": "canon.secret", "content": "RAW SECRET"}],
        selected_context="legacy",
        fallback_reason="not_ready",
    )

    operation, fields = events[0]
    assert operation == "retrieval.v2"
    assert fields["retrieval_v2_selected_context"] == "legacy"
    assert fields["retrieval_v2_semantic_gate"] == "calibration_thresholds_missing"
    assert "RAW SECRET" not in repr(fields)
    assert "canon.secret" not in repr(fields)


def test_retrieval_telemetry_allowlist_keeps_metrics_but_drops_unknown_content(tmp_path):
    path = tmp_path / "usage.jsonl"
    logger = UsageLogger(str(path))
    logger.routing_event(
        "retrieval.v2",
        status="completed",
        retrieval_v2_mode="shadow",
        retrieval_v2_selected_context="legacy",
        retrieval_legacy_selected=2,
        retrieval_v2_selected=3,
        retrieval_overlap_count=1,
        retrieval_overlap_rate=0.25,
        retrieval_embedding_requests=1,
        retrieval_embedding_prompt_tokens=42,
        retrieval_elapsed_ms=17,
        retrieval_legacy_id_hashes=["abc123"],
        retrieval_v2_id_hashes=["def456"],
        content="must-not-log",
        query="must-not-log-either",
    )
    logger.close()

    text = path.read_text()
    assert "must-not-log" not in text
    row = json.loads(text)
    assert row["operation"] == "retrieval.v2"
    assert row["retrieval_v2_mode"] == "shadow"
    assert row["retrieval_v2_selected_context"] == "legacy"
    assert row["retrieval_embedding_prompt_tokens"] == 42
    assert row["retrieval_legacy_id_hashes"] == ["abc123"]
