import asyncio
from types import MethodType, SimpleNamespace

import pytest

from hina_bot.ai.freshness import FreshnessMode
from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.information_plan import InformationPlan
from hina_bot.ai.information_routing import InformationRoute
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.ai.rp_output_policy import ProvenanceMode
from hina_bot.core.evidence_sufficiency import EvidenceAssessment
from hina_bot.core.retrieval_v2 import KnowledgeBundle


@pytest.mark.asyncio
async def test_shadow_start_does_not_wait_for_retrieval_completion():
    pipeline = object.__new__(InformationPipeline)
    pipeline._retrieval_shadow_tasks = set()
    started = asyncio.Event()
    release = asyncio.Event()

    async def observe(self, *args, **kwargs):
        started.set()
        await release.wait()

    pipeline._observe_retrieval_v2 = MethodType(observe, pipeline)
    pipeline._emit_retrieval_v2 = lambda *args, **kwargs: None

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
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not pipeline._retrieval_shadow_tasks


def test_shadow_backpressure_skips_new_work_and_records_reason():
    pipeline = object.__new__(InformationPipeline)
    pipeline._retrieval_shadow_tasks = {object(), object()}
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

    assert len(events) == 1
    assert events[0][0][1] == "skipped_backpressure"
    assert events[0][1]["fallback_reason"] == "backpressure"


def test_active_result_attaches_typed_bundle_and_recomputes_local_then_web():
    pipeline = object.__new__(InformationPipeline)
    pipeline.settings = SimpleNamespace(chat_web_search=True)
    bundle = KnowledgeBundle()
    result = SimpleNamespace(
        bundle=bundle,
        evidence=EvidenceAssessment(True, "matched_direct_claim"),
    )
    plan = InformationPlan(
        routing=RoutingPlan("호시노랑 무슨 사이야?", "호시노랑 무슨 사이야?"),
        route=InformationRoute.LOCAL_THEN_WEB,
        references=({"reference": "legacy"},),
        freshness=FreshnessMode.STATIC,
        fact_question=True,
        search_mode="required",
        provenance=ProvenanceMode.NATURAL_LOOKUP,
        search_reason="legacy_missing",
    )

    activated = pipeline._activate_retrieval_v2(plan, result)

    assert activated.retrieval_v2_applied
    assert activated.retrieval_v2_bundle is bundle
    assert activated.search_mode == "none"
    assert activated.search_reason == "matched_direct_claim"
    assert activated.search_decision_source == "retrieval_v2"
    assert activated.provenance == ProvenanceMode.SILENT


def test_rollout_telemetry_has_gate_and_no_content_fields():
    pipeline = object.__new__(InformationPipeline)
    events = []
    pipeline.usage = SimpleNamespace(
        routing_event=lambda operation, **fields: events.append((operation, fields))
    )
    pipeline.retrieval_v2 = SimpleNamespace(
        semantic_ready=False,
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
