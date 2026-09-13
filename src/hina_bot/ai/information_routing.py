from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .freshness import FreshnessMode, classify_freshness, is_live_domain
from .information_intent import (
    lore_query,
    personal_context,
    relation_or_event,
    self_identity,
    self_profile,
    simple_world_fact,
    world_fact,
)
from .rp_output_policy import SOURCE_REQUEST_QUERY


class InformationRoute(StrEnum):
    MEMORY = "memory"
    CLOCK = "clock"
    LOCAL_LORE = "local_lore"
    LOCAL_THEN_WEB = "local_then_web"
    WEB = "web"
    GENERAL = "general"


@dataclass(frozen=True)
class InformationRequest:
    route: InformationRoute
    lore_query: str
    freshness: FreshnessMode
    world_fact_question: bool = False
    relation_or_event: bool = False
    self_profile: bool = False
    explicit_source: bool = False


def classify_information_request(
    content: str,
    *,
    freshness: FreshnessMode | None = None,
) -> InformationRequest:
    text = content.strip()
    freshness = freshness or classify_freshness(text)
    personal = personal_context(text)
    identity = self_identity(text)
    profile = self_profile(text)
    relation = relation_or_event(text)
    simple_fact = simple_world_fact(text)
    fact_question = world_fact(text)
    explicit_source = bool(SOURCE_REQUEST_QUERY.search(text))
    query = lore_query(text, profile=profile, relation=relation)

    if personal:
        route = InformationRoute.MEMORY
    elif identity:
        route = InformationRoute.GENERAL
    elif explicit_source:
        route = InformationRoute.WEB
    elif freshness == FreshnessMode.CLOCK:
        route = InformationRoute.CLOCK
    elif freshness == FreshnessMode.REQUIRED and is_live_domain(text):
        route = InformationRoute.WEB
        fact_question = False
    elif profile:
        route = InformationRoute.LOCAL_LORE
    elif relation or simple_fact:
        route = InformationRoute.LOCAL_THEN_WEB
    elif freshness == FreshnessMode.REQUIRED:
        route = InformationRoute.WEB
    else:
        route = InformationRoute.GENERAL

    return InformationRequest(
        route=route,
        lore_query=query,
        freshness=freshness,
        world_fact_question=fact_question,
        relation_or_event=relation,
        self_profile=profile,
        explicit_source=explicit_source,
    )


def looks_like_relation_or_event_question(content: str) -> bool:
    return relation_or_event(content)


def looks_like_world_fact_question(content: str) -> bool:
    return world_fact(content)
