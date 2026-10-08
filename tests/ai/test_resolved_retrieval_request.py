import pytest

from hina_bot.ai.retrieval_request import (
    build_resolved_retrieval_request,
    build_retrieval_request,
)
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.entity_resolution import DEFAULT_ENTITIES, CanonicalEntity, EntityResolver
from hina_bot.core.retrieval_v2 import RetrievalIntent

PAIR = {"character.hina", "character.hoshino"}


@pytest.mark.parametrize("text", ["호시노랑 무슨 사이야?", "Hoshino", "히나야, 호시노는 선배야?"])
def test_rp_subject_and_counterpart_form_canonical_constraint(text):
    request = build_resolved_retrieval_request(RoutingPlan(text, text))
    assert set(request.entities) == PAIR
    assert set(request.required_entities) == PAIR
    assert request.visible_text == text
    assert not build_retrieval_request(RoutingPlan(text, text)).entities


def test_self_profile_subject_is_configured_not_guessed_from_first_person():
    request = build_resolved_retrieval_request(RoutingPlan("학년은?", "학년은?"))
    assert request.intent == RetrievalIntent.PROFILE
    assert request.required_entities == ("character.hina",)
    request = build_resolved_retrieval_request(RoutingPlan("나는 선생이야", "나는 선생이야"))
    assert request.entities == ("character.hina",)
    assert not request.required_entities
    with pytest.raises(ValueError):
        build_resolved_retrieval_request(RoutingPlan("", ""), rp_subject="히나")


def test_no_rp_subject_supports_standalone_lookup():
    request = build_resolved_retrieval_request(RoutingPlan("Hoshino", "Hoshino"), rp_subject=None)
    assert request.entities == request.required_entities == ("character.hoshino",)


def test_authorized_causal_followup_inherits_anchor_not_expanded_query():
    routing = RoutingPlan("그럼 무슨 사이야?", "lexical hints", "호시노는?", "explicit_reply")
    request = build_resolved_retrieval_request(routing)
    assert set(request.required_entities) == PAIR
    assert (request.anchor_text, request.anchor_source) == (routing.anchor, routing.anchor_source)
    for routing in (
        RoutingPlan("안녕", "Hoshino"),
        RoutingPlan("그럼 무슨 사이야?", "Hoshino", "Hoshino", ""),
        RoutingPlan("오늘 점심 맛있었어", "Hoshino", "Hoshino", "explicit_reply"),
    ):
        assert not build_resolved_retrieval_request(routing).required_entities


def test_ambiguity_does_not_fall_back_to_old_anchor():
    resolver = EntityResolver((*DEFAULT_ENTITIES, CanonicalEntity(
        "character.other", "다른 인물", ("호시노",),
    )))
    request = build_resolved_retrieval_request(
        RoutingPlan("그럼 호시노는?", "", "소라사키 히나", "explicit_reply"), resolver=resolver,
    )
    assert not request.required_entities
    request = build_resolved_retrieval_request(
        RoutingPlan("그럼 무슨 사이야?", "", "호시노", "explicit_reply"), resolver=resolver,
    )
    assert not request.required_entities


def test_explicit_pair_and_topic_change_do_not_add_old_or_rp_entity_to_constraint():
    resolver = EntityResolver((*DEFAULT_ENTITIES, CanonicalEntity(
        "character.other", "다른인물", (),
    )))
    request = build_resolved_retrieval_request(RoutingPlan(
        "호시노와 다른인물은?", "", "히나", "explicit_reply",
    ), resolver=resolver)
    assert set(request.required_entities) == {"character.hoshino", "character.other"}
    request = build_resolved_retrieval_request(RoutingPlan(
        "그럼 다른인물은?", "", "호시노", "explicit_reply",
    ), resolver=resolver)
    assert set(request.required_entities) == {"character.hina", "character.other"}
    request = build_resolved_retrieval_request(RoutingPlan(
        "히나 호시노 다른인물", "",
    ), resolver=resolver)
    assert not request.required_entities


@pytest.mark.parametrize("text", ["그럼 아코는?", "그럼 Unknown은?", "그럼 히나타는?"])
def test_unregistered_new_topic_does_not_inherit_old_character(text):
    request = build_resolved_retrieval_request(RoutingPlan(
        text, "호시노 " + text, "호시노는?", "explicit_reply",
    ))
    assert not request.required_entities
    assert "character.hoshino" not in request.entities


def test_explicit_rp_character_as_topic_also_overrides_anchor():
    request = build_resolved_retrieval_request(RoutingPlan(
        "그럼 히나는?", "호시노 그럼 히나는?", "호시노는?", "explicit_reply",
    ))
    assert request.required_entities == ("character.hina",)
