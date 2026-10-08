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

# Only used as a conservative blocker for the implicit RP-subject pair. If a relation
# question visibly names two slots but one is outside the small reviewed registry, a
# single resolved name must not be silently paired with Hina.
_EXPLICIT_RELATION_PAIR = re.compile(
    r"(?P<left>[^\s,!?？！，]+)\s*(?:와|과|랑|이랑|하고)\s*"
    r"(?P<right>[^\s,!?？！，]+?)(?:은|는|이|가)?\s*"
    r"(?:(?:무슨|어떤)\s*)?(?:사이|관계)",
    re.IGNORECASE,
)


def _strip_call_prefix(content: str, call_prefixes: tuple[str, ...] | None) -> str:
    text = content.lstrip()
    matched = max(
        (prefix for prefix in (call_prefixes or ()) if text.startswith(prefix)),
        key=len,
        default=None,
    )
    if matched is not None:
        text = text[len(matched):].lstrip(" \t\n,:：!！?？~")
    return text


def _has_partial_explicit_relation_pair(text: str, resolver: EntityResolver) -> bool:
    """Return True when a two-slot relation phrase is only partially resolvable."""
    match = _EXPLICIT_RELATION_PAIR.search(text)
    if match is None:
        return False
    resolved = [
        resolver.resolve_alias(match.group("left")),
        resolver.resolve_alias(match.group("right")),
    ]
    return sum(entity_id is not None for entity_id in resolved) != 2


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

    entities contains the canonical characters actually resolved for this retrieval
    context. required_entities is narrower: it is populated only for a complete,
    unambiguous relationship pair. A configured RP subject may complete an implicit
    "X랑 무슨 사이야?" pair, but it is not injected into every factual/profile query.
    Partial two-name questions never synthesize the missing counterpart as Hina.
    """
    resolver = resolver if resolver is not None else EntityResolver()
    if rp_subject is not None and rp_subject not in resolver.entities:
        raise ValueError("RP subject must be a registered canonical entity")

    request = build_retrieval_request(routing, call_prefixes=call_prefixes)
    topic_text = _strip_call_prefix(routing.visible_content, call_prefixes)
    resolution = resolver.resolve(topic_text)
    mentioned = resolution.entities
    partial_pair = (
        request.intent == RetrievalIntent.RELATIONSHIP_OR_EVENT
        and _has_partial_explicit_relation_pair(topic_text, resolver)
    )

    if (
        not resolution.ambiguous_aliases
        and not mentioned
        and not partial_pair
        and routing.anchor
        and routing.anchor_source
        and is_followup(routing.visible_content)
        and _ENTITY_FOLLOWUP.fullmatch(routing.visible_content)
    ):
        inherited = resolver.resolve(routing.anchor)
        if not inherited.ambiguous_aliases:
            mentioned = tuple(dict.fromkeys((*mentioned, *inherited.entities)))
        else:
            resolution = inherited

    entities = list(mentioned)
    required: tuple[str, ...] = ()
    if not resolution.ambiguous_aliases:
        if request.intent == RetrievalIntent.PROFILE and rp_subject and not mentioned:
            # The configured RP subject identifies whose profile is requested, but this
            # is not a relation evidence constraint.
            entities.append(rp_subject)
        elif request.intent == RetrievalIntent.RELATIONSHIP_OR_EVENT:
            if len(mentioned) == 2 and not partial_pair:
                required = mentioned
            elif (
                len(mentioned) == 1
                and not partial_pair
                and rp_subject is not None
                and mentioned[0] != rp_subject
            ):
                required = tuple(dict.fromkeys((rp_subject, mentioned[0])))
                entities.append(rp_subject)

    return replace(
        request,
        entities=tuple(dict.fromkeys(entities)),
        required_entities=required,
    )
