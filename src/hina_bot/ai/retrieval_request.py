"""Deterministic bridge from authorized production routing inputs to the v2 contract."""

from hina_bot.core.retrieval_v2 import RetrievalIntent, RetrievalRequest

from .information_routing import classify_information_request
from .routing_plan import RoutingPlan


def build_retrieval_request(
    routing: RoutingPlan,
    *,
    call_prefixes: tuple[str, ...] | None = None,
    entities: tuple[str, ...] = (),
    required_entities: tuple[str, ...] = (),
) -> RetrievalRequest:
    information = classify_information_request(
        routing.routing_query, call_prefixes=call_prefixes,
    )
    if information.self_profile:
        intent = RetrievalIntent.PROFILE
    elif information.relation_or_event:
        intent = RetrievalIntent.RELATIONSHIP_OR_EVENT
    elif information.world_fact_question:
        intent = RetrievalIntent.FACT
    else:
        intent = RetrievalIntent.CONVERSATION
    return RetrievalRequest(
        visible_text=routing.visible_content,
        retrieval_text=information.lore_query,
        anchor_text=routing.anchor,
        anchor_source=routing.anchor_source,
        entities=entities,
        required_entities=required_entities,
        intent=intent,
    )
