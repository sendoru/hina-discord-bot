from dataclasses import replace

import pytest

from hina_bot.core.ambient_retrieval import (
    AmbientConfig,
    AmbientRetriever,
    AmbientScene,
    ambient_scene_query,
    eligible_ambient_candidates,
)
from hina_bot.core.knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
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
AMBIENT_IDS = {
    "canon.hina.rest_discomfort_pattern",
    "canon.hina.unfamiliar-with-ordinary-leisure.inference",
    "canon.hina.relaxes-around-teacher.inference",
    "canon.hina.relationship.inference.cautious_desire_spend_time",
    "canon.hina.values_praise_and_attention_in_private_time",
}


class FakeBackend:
    cache_key = "fake:ambient:v1:4"
    dimensions = 4

    def __init__(self):
        self.documents = []
        self.queries = []
        self.failure = False

    @staticmethod
    def _document_vector(text):
        return {
            "rest": (1, 0, 0, 0),
            "leisure": (0, 1, 0, 0),
            "praise": (0, 0, 1, 0),
        }[text]

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        if self.failure:
            raise RuntimeError("provider content must stay hidden")
        return EmbeddingResult(
            tuple(self._document_vector(text) for text in texts),
            EmbeddingUsage(20, 1),
        )

    async def embed_query(self, text):
        self.queries.append(text)
        if self.failure:
            raise RuntimeError("provider content must stay hidden")
        if "쉬" in text or "일은 내가" in text:
            vector = (1, 0, 0, 0)
        elif "쇼핑" in text or "놀러" in text:
            vector = (0, 1, 0, 0)
        elif "잘했" in text or "고생" in text:
            vector = (0, 0, 1, 0)
        else:
            vector = (0, 0, 0, 1)
        return EmbeddingResult((vector,), EmbeddingUsage(7, 1))


def candidate(identifier, semantic_text, **overrides):
    base = KnowledgeCandidate(
        candidate_id=identifier,
        source="static_lore",
        kind="interpretation",
        content=identifier,
        search_text=identifier,
        subjects=("히나",),
        keywords=(identifier,),
        lane="canon",
        usages=(KnowledgeUsage.FACTUAL, KnowledgeUsage.AMBIENT),
        entities=(HINA,),
        fact_type="inference",
        confidence="crosschecked",
        kr_release="confirmed",
        semantic_text=semantic_text,
        evidence_ids=("canon.evidence.one",),
    )
    return replace(base, **overrides)


def candidates():
    return [
        candidate("rest", "rest"),
        candidate("leisure", "leisure"),
        candidate("praise", "praise"),
    ]


def request(visible, *, anchor="", retrieval_text="lexical expansion should not leak"):
    return RetrievalRequest(
        visible_text=visible,
        retrieval_text=retrieval_text,
        anchor_text=anchor,
        anchor_source="explicit_reply" if anchor else "",
        entities=(HINA,),
        intent=RetrievalIntent.CONVERSATION,
    )


def retriever(backend=None, **config):
    backend = backend or FakeBackend()
    calibration = SemanticCalibration(backend.cache_key, 0.4, 0.9)
    return (
        AmbientRetriever(
            SemanticIndex(backend),
            config=AmbientConfig(calibration=calibration, **config),
        ),
        backend,
    )


def ids(result):
    return [row.candidate.candidate_id for row in result.rows]


def test_scene_query_uses_only_small_natural_scene_projection():
    scene = AmbientScene(
        request("정말?", anchor="오늘은 그냥 쉬어도 되잖아"),
        rp_entity=HINA,
        recent_turns=("계속 일하고 있었잖아", "오늘은 그냥 쉬어도 되잖아"),
        relationship_signal="선생과 편하게 대화하는 상황",
    )
    query = ambient_scene_query(scene)
    assert "계속 일하고 있었잖아" in query
    assert query.count("오늘은 그냥 쉬어도 되잖아") == 1
    assert "정말?" in query
    assert "선생과 편하게 대화하는 상황" in query
    assert "lexical expansion should not leak" not in query
    assert HINA not in query


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rp_entity": ""},
        {"rp_entity": HINA, "recent_turns": ("a", "b", "c")},
        {"rp_entity": HINA, "recent_turns": ("",)},
        {"rp_entity": HINA, "recent_turns": ("x" * 601,)},
        {"rp_entity": HINA, "relationship_signal": "x" * 301},
    ],
)
def test_scene_rejects_unbounded_or_invalid_projection(kwargs):
    with pytest.raises(ValueError):
        AmbientScene(request("안녕"), **kwargs)


def test_short_recent_fragment_does_not_delete_richer_current_turn():
    scene = AmbientScene(
        request("오늘은 쉬어도 돼"),
        rp_entity=HINA,
        recent_turns=("쉬어",),
    )
    query = ambient_scene_query(scene)
    assert "쉬어" in query
    assert "오늘은 쉬어도 돼" in query


def test_ambient_candidate_filter_is_hina_specific_reviewed_interpretation_only():
    rows = candidates()
    rejected = [
        candidate("other-entity", "rest", entities=("character.hoshino",)),
        candidate("wrong-lane", "rest", lane="community_meme"),
        candidate("not-inference", "rest", fact_type="fact_direct"),
        candidate("no-evidence", "rest", evidence_ids=()),
        candidate("factual-only", "rest", usages=(KnowledgeUsage.FACTUAL,)),
    ]
    selected = eligible_ambient_candidates([*rejected, *rows], rp_entity=HINA)
    assert [row.candidate_id for row in selected] == ["rest", "leisure", "praise"]


def test_curated_lore_has_small_evidence_linked_ambient_set():
    lore = LoreIndex.load()
    rows = lore.candidates(include_community=False)
    ambient = [
        row for row in rows
        if KnowledgeUsage.AMBIENT in row.retrieval_usages
    ]
    assert {row.candidate_id for row in ambient} == AMBIENT_IDS
    all_ids = {row.candidate_id for row in rows}
    for row in ambient:
        assert row.fact_type == "inference"
        assert row.entities == (HINA,)
        assert row.semantic_text
        assert row.evidence_ids
        assert set(row.evidence_ids) <= all_ids
        assert KnowledgeUsage.FACTUAL in row.retrieval_usages


@pytest.mark.parametrize(
    ("visible", "expected"),
    [
        ("오늘은 그냥 쉬어도 되잖아. 일은 내가 도와줄게.", ["rest"]),
        ("일 끝났으면 쇼핑몰 가서 좀 놀러 가자.", ["leisure"]),
        ("오늘 진짜 고생했어. 준비한 거 정말 잘했네.", ["praise"]),
        ("안녕, 오늘 날씨 괜찮네.", []),
    ],
)
async def test_precision_first_scene_activation_and_zero_result(visible, expected):
    engine, _ = retriever()
    result = await engine.retrieve(
        AmbientScene(request(visible), rp_entity=HINA),
        candidates(),
    )
    assert ids(result) == expected
    assert result.semantic_status == "available"


async def test_recent_turn_and_relationship_signal_can_supply_scene_meaning():
    engine, _ = retriever()
    scene = AmbientScene(
        request("그래."),
        rp_entity=HINA,
        recent_turns=("오늘은 일 그만하고 쉬자.",),
        relationship_signal="선생이 히나의 과로를 걱정하는 상황",
    )
    result = await engine.retrieve(scene, candidates())
    assert ids(result) == ["rest"]


async def test_no_calibration_or_wrong_entity_makes_no_embedding_call():
    backend = FakeBackend()
    scene = AmbientScene(request("좀 쉬어."), rp_entity=HINA)
    result = await AmbientRetriever(SemanticIndex(backend)).retrieve(scene, candidates())
    assert not result.rows
    assert result.semantic_status == "unavailable"
    assert not backend.documents and not backend.queries

    engine, backend = retriever()
    result = await engine.retrieve(
        AmbientScene(request("좀 쉬어."), rp_entity="character.hoshino"),
        candidates(),
    )
    assert not result.rows
    assert result.semantic_status == "not_needed"
    assert not backend.documents and not backend.queries


async def test_backend_failure_has_no_lexical_or_web_fallback():
    engine, backend = retriever()
    backend.failure = True
    result = await engine.retrieve(
        AmbientScene(request("좀 쉬어."), rp_entity=HINA),
        candidates(),
    )
    assert not result.rows
    assert result.semantic_status == "failed"
    assert "provider content" not in repr(result)


async def test_retrieval_has_no_mechanical_repeat_suppression_and_reuses_candidate_cache():
    engine, backend = retriever()
    scene = AmbientScene(request("좀 쉬자."), rp_entity=HINA)
    first = await engine.retrieve(scene, candidates())
    second = await engine.retrieve(scene, candidates())
    assert ids(first) == ids(second) == ["rest"]
    assert backend.documents == ["rest", "leisure", "praise"]
    assert len(backend.queries) == 2
    assert second.cache_hits == 3 and second.cache_misses == 0


def test_bundle_composer_applies_small_ambient_budget_and_section_dedup():
    rest = candidate("rest", "rest")
    leisure = candidate("leisure", "leisure")
    ranked = (
        RankedKnowledgeCandidate(1.0, 0, rest),
        RankedKnowledgeCandidate(0.9, 1, leisure),
    )
    bundle = BundleComposer({
        KnowledgeUsage.AMBIENT: UsageBudget(1, 1000),
    }).compose({
        KnowledgeUsage.AMBIENT: ranked,
    })
    assert [row.candidate.candidate_id for row in bundle.character_insights] == ["rest"]
    assert not bundle.facts and not bundle.relations and not bundle.reactions


async def test_calibration_backend_mismatch_fails_closed_without_calls():
    backend = FakeBackend()
    engine = AmbientRetriever(
        SemanticIndex(backend),
        config=AmbientConfig(
            calibration=SemanticCalibration("different-backend", 0.4, 0.9),
        ),
    )
    result = await engine.retrieve(
        AmbientScene(request("좀 쉬자."), rp_entity=HINA),
        candidates(),
    )
    assert not result.rows
    assert result.semantic_status == "calibration_mismatch"
    assert not backend.documents and not backend.queries
