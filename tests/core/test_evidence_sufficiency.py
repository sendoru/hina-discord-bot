from dataclasses import replace
from importlib.resources import files

from hina_bot.ai.retrieval_request import build_resolved_retrieval_request
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.evidence_sufficiency import (
    assess_local_evidence,
    evidence_requirement,
)
from hina_bot.core.knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
)
from hina_bot.core.lore import LoreIndex
from hina_bot.core.relationship_grounding import RelationshipGrounder
from hina_bot.core.retrieval_v2 import (
    KnowledgeBundle,
    RetrievalIntent,
    RetrievalRequest,
)

HINA = "character.hina"
HOSHINO = "character.hoshino"


def ranked(candidate, order=0):
    return RankedKnowledgeCandidate(1.0, order, candidate)


def request(text, *, entities=(HINA, HOSHINO), pair=(HINA, HOSHINO), intent=RetrievalIntent.RELATIONSHIP_OR_EVENT):
    return RetrievalRequest(
        text,
        text,
        entities=entities,
        relation_pair=pair,
        intent=intent,
    )


def packaged_candidates():
    base = LoreIndex.load().candidates(include_community=False)
    supplemental = LoreIndex.load(str(files("hina_bot").joinpath(
        "data/relationship_grounding.jsonl",
    ))).candidates(include_community=False)
    return [*supplemental, *base]


def by_id(rows):
    return {row.candidate_id: row for row in rows}


def test_requirement_extracts_predicate_direction_and_time_from_question_not_summary():
    req = request("히나는 호시노를 만나기 전부터 알고 있었어?")
    requirement = evidence_requirement(req)
    assert requirement.predicate == "awareness"
    assert requirement.subject == HINA and requirement.object == HOSHINO
    assert requirement.time_scope == "before_first_meeting"

    reverse = request(
        "호시노는 히나를 만나기 전부터 알고 있었어?",
        entities=(HOSHINO, HINA),
        pair=(HOSHINO, HINA),
    )
    requirement = evidence_requirement(reverse)
    assert requirement.subject == HOSHINO and requirement.object == HINA


def test_builder_direction_does_not_depend_on_mention_order():
    rows = by_id(packaged_candidates())
    awareness = rows["canon.hina.knows_hoshino_past_before_meeting"]
    bundle = KnowledgeBundle(facts=(ranked(awareness),))
    for text in (
        "히나가 호시노를 만나기 전부터 알고 있었어?",
        "호시노를 히나가 만나기 전부터 알고 있었어?",
    ):
        req = build_resolved_retrieval_request(RoutingPlan(text, text))
        assert assess_local_evidence(
            req, bundle, supporting_candidates=rows.values()
        ).sufficient, text

    for text in (
        "호시노가 히나를 만나기 전부터 알고 있었어?",
        "히나를 호시노가 만나기 전부터 알고 있었어?",
    ):
        req = build_resolved_retrieval_request(RoutingPlan(text, text))
        assert not assess_local_evidence(
            req, bundle, supporting_candidates=rows.values()
        ).sufficient, text


def test_reciprocal_relation_does_not_prove_one_direction():
    rows = by_id(packaged_candidates())
    awareness = rows["canon.hina.knows_hoshino_past_before_meeting"]
    text = "히나랑 호시노는 서로 만나기 전부터 알고 있었어?"
    req = build_resolved_retrieval_request(RoutingPlan(text, text))
    result = assess_local_evidence(
        req, KnowledgeBundle(facts=(ranked(awareness),)),
        supporting_candidates=rows.values(),
    )
    assert not result.sufficient
    assert result.reason == "entity_context_missing"


def test_builder_addressing_uses_grammatical_subject():
    rows = by_id(packaged_candidates())
    evidence = rows["canon.relationship_closer_after_fight_with_set_and_hoshino"]
    text = "히나를 호시노가 뭐라고 불렀어?"
    req = build_resolved_retrieval_request(RoutingPlan(text, text))
    result = assess_local_evidence(
        req, KnowledgeBundle(relations=(ranked(evidence),)),
        supporting_candidates=rows.values(),
    )
    assert result.sufficient


def test_first_meeting_evidence_does_not_answer_prior_awareness():
    rows = by_id(packaged_candidates())
    meeting = rows["canon.hina.first_meeting_with_hoshino_vol1"]
    req = request("히나는 호시노를 만나기 전부터 알고 있었어?")
    result = assess_local_evidence(
        req,
        KnowledgeBundle(facts=(ranked(meeting),)),
        supporting_candidates=rows.values(),
    )
    assert not result.sufficient
    assert result.reason == "proposition_not_covered"
    assert result.predicate == "awareness"


def test_direct_prior_awareness_claim_is_sufficient_independent_of_summary_wording():
    rows = by_id(packaged_candidates())
    awareness = rows["canon.hina.knows_hoshino_past_before_meeting"]
    changed_wording = replace(
        awareness,
        content="이 문장에는 만남·대화·접점 같은 legacy relation keyword가 없어도 된다.",
        search_text="완전히 다른 표현",
    )
    result = assess_local_evidence(
        request("히나는 호시노를 만나기 전부터 알고 있었어?"),
        KnowledgeBundle(facts=(ranked(changed_wording),)),
        supporting_candidates=rows.values(),
    )
    assert result.sufficient
    assert result.reason == "matched_direct_claim"
    assert result.matched_ids == ("canon.hina.knows_hoshino_past_before_meeting",)


def test_direction_mismatch_is_not_sufficient():
    rows = by_id(packaged_candidates())
    awareness = rows["canon.hina.knows_hoshino_past_before_meeting"]
    result = assess_local_evidence(
        request(
            "호시노는 히나를 만나기 전부터 알고 있었어?",
            entities=(HOSHINO, HINA),
            pair=(HOSHINO, HINA),
        ),
        KnowledgeBundle(facts=(ranked(awareness),)),
        supporting_candidates=rows.values(),
    )
    assert not result.sufficient
    assert result.reason == "proposition_not_covered"


def test_time_scope_prevents_historical_addressing_from_answering_current_state():
    rows = by_id(packaged_candidates())
    relation = rows["canon.relationship_closer_after_fight_with_set_and_hoshino"]
    current = request(
        "지금 호시노는 히나를 뭐라고 불러?",
        entities=(HOSHINO, HINA),
        pair=(HOSHINO, HINA),
    )
    result = assess_local_evidence(
        current,
        KnowledgeBundle(relations=(ranked(relation),)),
        supporting_candidates=rows.values(),
    )
    assert not result.sufficient
    assert result.reason == "proposition_not_covered"

    historical = request(
        "호시노는 히나를 뭐라고 불렀어?",
        entities=(HOSHINO, HINA),
        pair=(HOSHINO, HINA),
    )
    result = assess_local_evidence(
        historical,
        KnowledgeBundle(relations=(ranked(relation),)),
        supporting_candidates=rows.values(),
    )
    assert result.sufficient


def test_school_year_relation_can_be_derived_only_with_reviewed_support_chain():
    rows = by_id(packaged_candidates())
    relation = rows["canon.hina_hoshino.same_school_year"]
    req = request("호시노는 히나보다 선배야?")
    result = assess_local_evidence(
        req,
        KnowledgeBundle(relations=(ranked(relation),)),
        supporting_candidates=rows.values(),
    )
    assert result.sufficient
    assert result.reason == "matched_derived_claim"
    assert result.answer_state == "derived"

    result = assess_local_evidence(
        req,
        KnowledgeBundle(relations=(ranked(relation),)),
    )
    assert not result.sufficient


def test_explicit_unknown_is_answerable_but_not_promoted_to_positive_fact():
    rows = by_id(packaged_candidates())
    unknown = rows["canon.hina.eden_exact_knowledge_of_hoshino_trauma"]
    req = request("히나는 호시노의 트라우마를 정확히 알고 있었어?")
    result = assess_local_evidence(
        req,
        KnowledgeBundle(facts=(ranked(unknown),)),
        supporting_candidates=rows.values(),
    )
    assert result.sufficient
    assert result.reason == "matched_explicit_unknown"
    assert result.answer_state == "unknown"


def test_generic_relation_requires_a_matching_claim_not_merely_same_entity_pair():
    rows = by_id(packaged_candidates())
    meeting = rows["canon.hina.first_meeting_with_hoshino_vol1"]
    relation = rows["canon.relationship_closer_after_fight_with_set_and_hoshino"]
    req = request("히나랑 호시노는 무슨 사이야?")

    wrong = assess_local_evidence(
        req,
        KnowledgeBundle(relations=(ranked(meeting),)),
        supporting_candidates=rows.values(),
    )
    assert not wrong.sufficient

    right = assess_local_evidence(
        req,
        KnowledgeBundle(relations=(ranked(relation),)),
        supporting_candidates=rows.values(),
    )
    assert right.sufficient


def test_ambient_and_reaction_sections_never_satisfy_factual_web_fallback():
    direct = KnowledgeCandidate(
        candidate_id="ambient.direct",
        source="static_lore",
        kind="world_fact",
        content="direct",
        search_text="direct",
        subjects=(),
        keywords=(),
        usages=(KnowledgeUsage.AMBIENT,),
        lane="canon",
        fact_type="fact_direct",
        confidence="verified",
        kr_release="confirmed",
        source_metadata=(("type", "official_game"),),
    )
    req = RetrievalRequest("사실 질문", "사실 질문", intent=RetrievalIntent.FACT)
    result = assess_local_evidence(
        req,
        KnowledgeBundle(character_insights=(ranked(direct),)),
    )
    assert not result.sufficient


def test_trusted_direct_factual_row_suffices_but_unreviewed_runtime_row_does_not():
    reviewed = KnowledgeCandidate(
        candidate_id="canon.test",
        source="static_lore",
        kind="world_fact",
        content="fact",
        search_text="fact",
        subjects=(),
        keywords=(),
        usages=(KnowledgeUsage.FACTUAL,),
        lane="canon",
        fact_type="fact_direct",
        confidence="crosschecked",
        kr_release="confirmed",
        source_metadata=(("type", "official_game"),),
    )
    runtime = replace(
        reviewed,
        candidate_id="runtime_lore.test",
        source="runtime_knowledge",
        lane=None,
        confidence=None,
        kr_release=None,
        source_metadata=(),
    )
    req = RetrievalRequest("안정적인 사실 질문", "안정적인 사실 질문", intent=RetrievalIntent.FACT)
    assert assess_local_evidence(req, KnowledgeBundle(facts=(ranked(reviewed),))).sufficient
    assert not assess_local_evidence(req, KnowledgeBundle(facts=(ranked(runtime),))).sufficient


def test_awareness_claim_does_not_pollute_generic_exact_relation_grounding():
    grounder = RelationshipGrounder.load()
    req = request("히나는 호시노를 만나기 전부터 알고 있었어?")
    ids = [row.candidate.candidate_id for row in grounder.ground(req)]
    assert "canon.hina.knows_hoshino_past_before_meeting" not in ids
