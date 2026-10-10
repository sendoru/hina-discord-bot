import pytest

from hina_bot.ai.information_routing import classify_information_request
from hina_bot.ai.retrieval_request import build_retrieval_request
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.retrieval_v2 import RetrievalIntent


@pytest.mark.parametrize("text,intent", [
    ("히나야 생일 언제야?", RetrievalIntent.PROFILE),
    ("호시노랑 무슨 사이야?", RetrievalIntent.RELATIONSHIP_OR_EVENT),
    ("마코토는 어디 소속이야?", RetrievalIntent.FACT),
    ("히나야 안녕", RetrievalIntent.CONVERSATION),
])
def test_request_reuses_existing_deterministic_intent_and_query(text, intent):
    routing = RoutingPlan(text, text)
    request = build_retrieval_request(routing, call_prefixes=("히나야",))
    information = classify_information_request(text, call_prefixes=("히나야",))
    assert request.intent == intent
    assert request.visible_text == text
    assert request.retrieval_text == information.lore_query
    assert not request.entities and not request.required_entities


def test_followup_preserves_visible_turn_causal_anchor_and_resolved_ids_separately():
    routing = RoutingPlan(
        visible_content="정말?", routing_query="호시노랑 무슨 사이야? 정말?",
        anchor="호시노랑 무슨 사이야?", anchor_source="explicit_reply",
    )
    entities = ("character.hina", "character.hoshino")
    request = build_retrieval_request(routing, entities=entities, required_entities=entities)
    assert request.visible_text == "정말?"
    assert request.anchor_text == routing.anchor
    assert request.anchor_source == routing.anchor_source
    assert request.intent == RetrievalIntent.RELATIONSHIP_OR_EVENT
    assert request.entities == entities and request.required_entities == entities
    assert request.retrieval_text != request.visible_text
