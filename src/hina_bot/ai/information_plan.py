"""Immutable information/evidence decisions for one chat turn."""

from dataclasses import dataclass

from .freshness import FreshnessMode
from .information_routing import InformationRoute
from .routing_plan import RoutingPlan
from .rp_output_policy import ProvenanceMode


@dataclass(frozen=True)
class InformationPlan:
    """Decisions made before prompt assembly for one resolved routing query."""

    routing: RoutingPlan
    route: InformationRoute
    references: tuple[dict, ...]
    freshness: FreshnessMode
    fact_question: bool
    search_mode: str
    provenance: ProvenanceMode


__all__ = ["InformationPlan"]
