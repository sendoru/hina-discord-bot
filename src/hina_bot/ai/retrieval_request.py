"""Deterministic bridge from authorized production routing inputs to the v2 contract."""

import re
from dataclasses import replace

from hina_bot.core.entity_resolution import HINA_ENTITY_ID, EntityResolver
from hina_bot.core.retrieval_v2 import RetrievalIntent, RetrievalRequest

from .contextual_routing import is_followup
from .information_routing import classify_information_request
from .routing_plan import RoutingPlan

# A narrower condition than general routing inheritance: unknown/new names must not
# silently become the old character. Extend these elliptical forms through evals.
_ENTITY_FOLLOWUP = re.compile(
    r"^\s*(?:(?:그럼|그러면|그렇다면|그래서|근데|그런데)\s*)?"
    r"(?:(?:걔|얘|쟤)(?:는|가|랑)?\s*)?"
    r"(?:무슨\s*사이야|어떤\s*관계야|몇\s*학년(?:이야)?|학년은|"
    r"어디\s*소속이야|누구야|왜|어떻게|언제|어디|뭐|무엇|얼마|서로\s*알아|친해)"
    r"\s*[?？!.~]*\s*$"
)


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


def build_resolved_retrieval_request(
    routing: RoutingPlan,
    *,
    resolver: EntityResolver | None = None,
    rp_subject: str | None = HINA_ENTITY_ID,
    call_prefixes: tuple[str, ...] | None = None,
) -> RetrievalRequest:
    """Opt-in entity bridge; the existing builder/production path stays unchanged.

    The configured RP subject is an identity, not a name guessed from user first-person
    text. One named counterpart grounds that RP pair; two explicit characters ground
    that pair. Ambiguity or more than two targets leaves the evidence constraint empty.
    Authorized anchors are inherited only for a recognized name-free elliptical follow-up.
    Normalized lexical queries and classifier context never introduce new entities.
    """
    resolver = resolver if resolver is not None else EntityResolver()
    if rp_subject is not None and rp_subject not in resolver.entities:
        raise ValueError("RP subject must be a registered canonical entity")
    rp_entities = (rp_subject,) if rp_subject else ()
    request = build_retrieval_request(routing, call_prefixes=call_prefixes)
    resolution = resolver.resolve(routing.visible_content)
    mentioned = resolution.entities
    if (not resolution.ambiguous_aliases
            and not mentioned
            and routing.anchor and routing.anchor_source and is_followup(routing.visible_content)
            and _ENTITY_FOLLOWUP.fullmatch(routing.visible_content)):
        inherited = resolver.resolve(routing.anchor)
        if not inherited.ambiguous_aliases:
            mentioned = tuple(dict.fromkeys((*mentioned, *inherited.entities)))
        else:
            resolution = inherited
    required = ()
    if not resolution.ambiguous_aliases:
        if len(mentioned) == 1:
            required = tuple(dict.fromkeys((*rp_entities, *mentioned)))
        elif len(mentioned) == 2:
            required = mentioned
        elif not mentioned and request.intent == RetrievalIntent.PROFILE and rp_subject:
            required = (rp_subject,)
    entities = tuple(dict.fromkeys((*rp_entities, *mentioned)))
    return replace(request, entities=entities, required_entities=required)
