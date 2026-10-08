import pytest

from hina_bot.ai.retrieval_request import (
    build_resolved_retrieval_request,
    build_retrieval_request,
)
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.entity_resolution import DEFAULT_ENTITIES, CanonicalEntity, EntityResolver
from hina_bot.core.retrieval_v2 import RetrievalIntent

PAIR = {"character.hina", "character.hoshino"}


@pytest.mark.parametrize("text", ["호시노랑 무슨 사이야?", "히나와 호시노는 어떤 관계야?"])
def test_complete_relation_pair_forms_canonical_constraint(text):
    request = build_resolved_retrieval_request(RoutingPlan(text, text))
    assert set(request.entities) == PAIR
    assert set(request.relation_pair) == PAIR
    assert request.visible_text == text
    assert not build_retrieval_request(RoutingPlan(text, text)).entities


def test_call_prefix_is_invocation_not_an_extra_relation_target():
    text = "히나야 호시노랑 무슨 사이야?"
    request = build_resolved_retrieval_request(
        RoutingPlan(text, text), call_prefixes=("히나야",),
    )
    assert set(request.entities) == PAIR
    assert set(request.relation_pair) == PAIR


def test_self_profile_subject_is_context_entity_not_relation_constraint():
    request = build_resolved_retrieval_request(RoutingPlan("학년은?", "학년은?"))
    assert request.intent == RetrievalIntent.PROFILE
    assert request.entities == ("character.hina",)
    assert not request.relation_pair

    request = build_resolved_retrieval_request(RoutingPlan("나는 선생이야", "나는 선생이야"))
    assert not request.entities
    assert not request.relation_pair

    with pytest.raises(ValueError):
        build_resolved_retrieval_request(RoutingPlan("", ""), rp_subject="히나")


def test_single_factual_target_is_not_a_relation_pair_constraint():
    request = build_resolved_retrieval_request(
        RoutingPlan("호시노 몇 학년이야?", "호시노 몇 학년이야?"),
    )
    assert request.entities == ("character.hoshino",)
    assert not request.relation_pair


def test_no_rp_subject_keeps_standalone_entity_without_synthesizing_constraint():
    request = build_resolved_retrieval_request(
        RoutingPlan("Hoshino", "Hoshino"), rp_subject=None,
    )
    assert request.entities == ("character.hoshino",)
    assert not request.relation_pair


def test_authorized_causal_followup_inherits_anchor_for_complete_relation_pair():
    routing = RoutingPlan("그럼 무슨 사이야?", "lexical hints", "호시노는?", "explicit_reply")
    request = build_resolved_retrieval_request(routing)
    assert set(request.relation_pair) == PAIR
    assert (request.anchor_text, request.anchor_source) == (routing.anchor, routing.anchor_source)
    for routing in (
        RoutingPlan("안녕", "Hoshino"),
        RoutingPlan("그럼 무슨 사이야?", "Hoshino", "Hoshino", ""),
        RoutingPlan("오늘 점심 맛있었어", "Hoshino", "Hoshino", "explicit_reply"),
    ):
        assert not build_resolved_retrieval_request(routing).relation_pair


@pytest.mark.parametrize(
    "text",
    ["그럼 왜?", "그럼 어떻게?", "그럼 언제?", "그럼 뭐야?", "그럼 얼마야?"],
)
def test_open_ended_followups_do_not_inherit_anchor_identity_for_exact_grounding(text):
    request = build_resolved_retrieval_request(RoutingPlan(
        text, "호시노 " + text, "호시노는?", "explicit_reply",
    ))
    assert not request.entities
    assert not request.relation_pair


def test_broad_relationship_paraphrase_stays_in_semantic_factual_lane():
    text = "호시노랑 예전부터 친했던 거야?"
    request = build_resolved_retrieval_request(RoutingPlan(text, text))
    assert request.entities == ("character.hoshino",)
    assert not request.relation_pair
    assert request.intent == RetrievalIntent.CONVERSATION


def test_high_precision_seniority_cue_still_forms_implicit_rp_pair():
    text = "호시노는 선배야?"
    request = build_resolved_retrieval_request(RoutingPlan(text, text))
    assert set(request.entities) == PAIR
    assert set(request.relation_pair or ()) == PAIR


def test_ambiguity_does_not_fall_back_to_old_anchor():
    resolver = EntityResolver((*DEFAULT_ENTITIES, CanonicalEntity(
        "character.other", "다른 인물", ("호시노",),
    )))
    request = build_resolved_retrieval_request(
        RoutingPlan("그럼 호시노는?", "", "소라사키 히나", "explicit_reply"), resolver=resolver,
    )
    assert not request.relation_pair
    request = build_resolved_retrieval_request(
        RoutingPlan("그럼 무슨 사이야?", "", "호시노", "explicit_reply"), resolver=resolver,
    )
    assert not request.relation_pair


def test_explicit_pair_and_topic_change_do_not_add_old_or_rp_entity_to_constraint():
    resolver = EntityResolver((*DEFAULT_ENTITIES, CanonicalEntity(
        "character.other", "다른인물", (),
    )))
    request = build_resolved_retrieval_request(RoutingPlan(
        "호시노와 다른인물은 어떤 관계야?", "", "히나", "explicit_reply",
    ), resolver=resolver)
    assert set(request.entities) == {"character.hoshino", "character.other"}
    assert set(request.relation_pair) == {"character.hoshino", "character.other"}

    request = build_resolved_retrieval_request(RoutingPlan(
        "그럼 다른인물은?", "", "호시노", "explicit_reply",
    ), resolver=resolver)
    assert request.entities == ("character.other",)
    assert not request.relation_pair

    request = build_resolved_retrieval_request(RoutingPlan(
        "히나 호시노 다른인물", "",
    ), resolver=resolver)
    assert set(request.entities) == {"character.hina", "character.hoshino", "character.other"}
    assert not request.relation_pair


@pytest.mark.parametrize("text", ["그럼 아코는?", "그럼 Unknown은?", "그럼 히나타는?"])
def test_unregistered_new_topic_does_not_inherit_old_character(text):
    request = build_resolved_retrieval_request(RoutingPlan(
        text, "호시노 " + text, "호시노는?", "explicit_reply",
    ))
    assert not request.relation_pair
    assert "character.hoshino" not in request.entities


@pytest.mark.parametrize(
    ("text", "call_prefixes", "expected_entities"),
    [
        ("호시노랑 아코는 무슨 사이야?", None, ("character.hoshino",)),
        ("히나야 아코랑 무슨 사이야?", ("히나야",), ()),
    ],
)
def test_partial_known_unknown_relation_pair_never_substitutes_rp_subject(
    text, call_prefixes, expected_entities,
):
    request = build_resolved_retrieval_request(
        RoutingPlan(text, text), call_prefixes=call_prefixes,
    )
    assert request.entities == expected_entities
    assert not request.relation_pair


def test_explicit_rp_character_as_topic_does_not_become_relation_constraint():
    request = build_resolved_retrieval_request(RoutingPlan(
        "그럼 히나는?", "호시노 그럼 히나는?", "호시노는?", "explicit_reply",
    ))
    assert request.entities == ("character.hina",)
    assert not request.relation_pair
