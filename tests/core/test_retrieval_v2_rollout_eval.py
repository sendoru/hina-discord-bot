from pathlib import Path

import pytest

from hina_bot.ai.retrieval_request import build_resolved_retrieval_request
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.ambient_retrieval import AmbientSceneContext
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate, KnowledgeUsage
from hina_bot.core.lore import LoreIndex, read_jsonl
from hina_bot.core.retrieval_v2_runtime import RetrievalV2Engine
from hina_bot.core.semantic_retrieval import (
    EmbeddingResult,
    EmbeddingUsage,
    SemanticCalibration,
    SemanticIndex,
)


class EvalEmbedding:
    cache_key = "fake:rollout-eval:v1:2"
    dimensions = 2

    async def embed_candidates(self, texts):
        return EmbeddingResult(
            tuple((1.0, 0.0) for _ in texts),
            EmbeddingUsage(len(texts), 1),
        )

    async def embed_query(self, text):
        vector = (-1.0, 0.0) if "점심" in text else (1.0, 0.0)
        return EmbeddingResult((vector,), EmbeddingUsage(1, 1))


def reaction_candidate():
    return KnowledgeCandidate(
        candidate_id="community.eval.reaction",
        source="runtime_knowledge",
        kind="optional_reaction",
        content="머리 크기 놀림 반응",
        search_text="머리 크기 놀림",
        subjects=("머리",),
        keywords=("머리", "크기", "놀림"),
        usages=(KnowledgeUsage.REACTION,),
        lane="community_meme",
    )


@pytest.mark.asyncio
async def test_rollout_fixture_covers_all_lanes_and_hard_negative():
    cases = read_jsonl(Path("evals/retrieval_v2_rollout.jsonl"))
    assert {case["lane"] for case in cases} == {
        "factual", "relation", "ambient", "reaction",
    }
    assert any(case["forbidden_ids"] for case in cases if "forbidden_ids" in case)

    for case in cases:
        backend = EvalEmbedding()
        calibration = SemanticCalibration(backend.cache_key, 0.5, 0.9)
        engine = RetrievalV2Engine(
            LoreIndex.load(),
            semantic_index=SemanticIndex(backend),
            factual_calibration=calibration,
            ambient_calibration=calibration,
        )
        request = build_resolved_retrieval_request(
            RoutingPlan(case["visible_text"], case["visible_text"]),
            call_prefixes=tuple(case.get("call_prefixes", ())),
        )
        runtime = (reaction_candidate(),) if case.get("inject_reaction") else ()
        scene = (
            AmbientSceneContext("character.hina")
            if case["lane"] == "ambient"
            else None
        )

        result = await engine.retrieve(
            request,
            runtime_candidates=runtime,
            scene=scene,
        )

        lane_rows = {
            "factual": result.bundle.facts,
            "relation": result.bundle.relations,
            "ambient": result.bundle.character_insights,
            "reaction": result.bundle.reactions,
        }[case["lane"]]
        selected = {row.candidate.candidate_id for row in lane_rows}
        for expected in case.get("expected_ids", ()):
            assert expected in selected, case["id"]
        for forbidden in case.get("forbidden_ids", ()):
            assert forbidden not in result.selected_ids, case["id"]
        if "expected_nonzero" in case:
            assert bool(lane_rows) is case["expected_nonzero"], case["id"]
        assert result.evidence.sufficient is case["expect_sufficient"], case["id"]
