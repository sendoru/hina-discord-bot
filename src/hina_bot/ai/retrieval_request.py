"""Deterministic bridge from authorized production routing inputs to the v2 contract."""

import re
from dataclasses import replace

from hina_bot.core.entity_resolution import HINA_ENTITY_ID, EntityResolver
from hina_bot.core.retrieval_v2 import RetrievalIntent, RetrievalRequest

from .information_routing import classify_information_request
from .routing_plan import RoutingPlan

# Only name-free forms that need exact profile/relation grounding inherit anchor identity.
# Open-ended why/how/when/what questions are deliberately left to factual semantic recall.
_ANCHOR_ELLIPSIS = re.compile(
    r"^\s*(?:(?:그럼|그러면|그렇다면|그래서|근데|그런데)\s*)?"
    r"(?:(?:걔|얘|쟤)(?:는|가|랑)?\s*)?"
    r"(?:"
    r"무슨\s*사이야|어떤\s*관계야|"
    r"몇\s*학년(?:이야)?|학년은|어디\s*소속이야|"
    r"선배야|후배야|동급생이야|같은\s*학년이야|"
    r"(?:뭐라고|어떻게)\s*불러"
    r")\s*[?？!.~]*\s*$",
    re.IGNORECASE,
)

# A single resolved name must not be paired with Hina when the visible relation syntax
# contains a second, unresolved named slot.
_EXPLICIT_RELATION_PAIR = re.compile(
    r"(?P<left>[^\s,!?？！，]+)\s*(?:와|과|랑|이랑|하고)\s*"
    r"(?!무슨(?:\s|$)|어떤(?:\s|$))"
    r"(?P<right>[^\s,!?？！，]+?)(?:은|는|이|가)?\s*"
    r"(?:(?:무슨|어떤)\s*)?(?:사이|관계)",
    re.IGNORECASE,
)

# Exact relationship grounding intentionally recognizes only high-precision relation,
# seniority and addressing cues. Broader "친했어/만났어/알았어" paraphrases belong to
# factual semantic retrieval and do not need a deterministic pair.
_RELATION_GROUNDING_QUERY = re.compile(
    r"(?:무슨|어떤)\s*(?:사이|관계)|"
    r"(?:선배|후배|동급생|같은\s*학년|호칭)|"
    r"(?:(?:뭐라고|어떻게|어떤)\s*부르)",
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
    match = _EXPLICIT_RELATION_PAIR.search(text)
    if match is None:
        return False
    resolved = (
        resolver.resolve_alias(match.group("left")),
        resolver.resolve_alias(match.group("right")),
    )
    return sum(entity_id is not None for entity_id in resolved) != 2


def build_retrieval_request(
    routing: RoutingPlan,
    *,
    call_prefixes: tuple[str, ...] | None = None,
    entities: tuple[str, ...] = (),
    relation_pair: tuple[str, str] | None = None,
) -> RetrievalRequest:
    information = classify_information_request(
        routing.routing_query,
        call_prefixes=call_prefixes,
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
        relation_pair=relation_pair,
        intent=intent,
    )


def build_resolved_retrieval_request(
    routing: RoutingPlan,
    *,
    resolver: EntityResolver | None = None,
    rp_subject: str | None = HINA_ENTITY_ID,
    call_prefixes: tuple[str, ...] | None = None,
) -> RetrievalRequest:
    """Resolve only high-confidence current/causal identities for v2 retrieval.

    Current-turn explicit entities win. A reviewed RP subject may complete one explicit
    counterpart only for high-precision relation grounding. Anchor identity is inherited
    only for a small name-free profile/relation ellipsis. Uncertainty remains unresolved.
    """
    resolver = resolver if resolver is not None else EntityResolver()
    if rp_subject is not None and rp_subject not in resolver.entities:
        raise ValueError("RP subject must be a registered canonical entity")

    request = build_retrieval_request(routing, call_prefixes=call_prefixes)
    topic_text = _strip_call_prefix(routing.visible_content, call_prefixes)
    resolution = resolver.resolve(topic_text)
    mentioned = resolution.entities
    relation_grounding = (
        request.intent in {
            RetrievalIntent.RELATIONSHIP,
            RetrievalIntent.EVENT,
            RetrievalIntent.RELATIONSHIP_OR_EVENT,
        }
        or bool(_RELATION_GROUNDING_QUERY.search(topic_text))
    )
    partial_pair = (
        relation_grounding
        and _has_partial_explicit_relation_pair(topic_text, resolver)
    )

    if (
        not resolution.ambiguous_aliases
        and not mentioned
        and not partial_pair
        and routing.anchor
        and routing.anchor_source
        and _ANCHOR_ELLIPSIS.fullmatch(topic_text)
    ):
        inherited = resolver.resolve(routing.anchor)
        if not inherited.ambiguous_aliases:
            mentioned = inherited.entities
        else:
            resolution = inherited

    entities = list(mentioned)
    pair: tuple[str, str] | None = None
    if not resolution.ambiguous_aliases:
        if request.intent == RetrievalIntent.PROFILE and rp_subject and not mentioned:
            entities.append(rp_subject)
        elif relation_grounding:
            if len(mentioned) == 2 and not partial_pair:
                pair = (mentioned[0], mentioned[1])
            elif (
                len(mentioned) == 1
                and not partial_pair
                and rp_subject is not None
                and mentioned[0] != rp_subject
            ):
                pair = (rp_subject, mentioned[0])
                entities.append(rp_subject)

    return replace(
        request,
        entities=tuple(dict.fromkeys(entities)),
        relation_pair=pair,
    )
