from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .freshness import FreshnessMode


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
