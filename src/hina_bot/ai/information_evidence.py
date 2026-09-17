from dataclasses import dataclass

from .freshness import FreshnessMode, needs_location_clarification
from .information_routing import InformationRoute

_RELATION_TERMS = (
    "만났", "만난", "만남", "대면", "대화", "친분", "관계", "접점",
    "방문", "출입", "들어갔", "들어간", "참여", "동행", "목격", "대치", "사건",
)


@dataclass(frozen=True)
class SearchDecision:
    mode: str
    locked: bool
    reason: str


def enough_local(request, references):
    facts = [row for row in references if row.get("kind") == "world_fact"
             and row.get("awareness") not in {"audience_only", "inference", "unknown"}]
    if not facts:
        return False
    if not request.relation_or_event:
        return True
    return any(any(term in (row.get("reference", "") + " " + row.get("content", ""))
                   for term in _RELATION_TERMS) for row in facts)


def trusted_local(references):
    return any(
        str(row.get("reference", "")).startswith(("canon.", "runtime_lore."))
        for row in references
        if row.get("kind") == "world_fact"
    )


def search_decision(request, references, *, enabled, default_location="") -> SearchDecision:
    """Return the deterministic baseline plus whether semantic routing may override it."""

    if not enabled:
        return SearchDecision("none", True, "disabled")
    if request.route in {
        InformationRoute.MEMORY, InformationRoute.CLOCK, InformationRoute.LOCAL_LORE,
    }:
        return SearchDecision("none", True, "local_only")
    if request.explicit_source:
        return SearchDecision("required", True, "explicit_source")
    if request.route == InformationRoute.WEB:
        if (request.freshness == FreshnessMode.REQUIRED
                and needs_location_clarification(request.lore_query)
                and not default_location):
            # Do not let a semantic classifier force a location-dependent search without a location.
            return SearchDecision("auto", True, "missing_location")
        return SearchDecision("required", True, "deterministic_web")
    if request.route == InformationRoute.LOCAL_THEN_WEB:
        if not enough_local(request, references):
            return SearchDecision("required", True, "local_evidence_missing")
        if request.relation_or_event and not trusted_local(references):
            return SearchDecision("required", True, "trusted_lore_missing")
        return SearchDecision("none", True, "local_evidence_sufficient")
    if request.route == InformationRoute.GENERAL and request.factual_challenge:
        # A disagreement alone is not proof that the previous answer was wrong. Give the final model
        # access to verification without forcing a lookup for every correction or stable fact.
        return SearchDecision("auto", True, "factual_challenge")
    if request.freshness == FreshnessMode.AUTO:
        return SearchDecision("auto", False, "semantic_temporal")
    # GENERAL + STATIC is intentionally open: stable questions usually remain none, but a semantic
    # classifier can catch version/status questions whose time dependence is not expressed by a
    # small set of freshness keywords.
    return SearchDecision("none", False, "semantic_open")


def search_mode(request, references, *, enabled, default_location=""):
    """Compatibility wrapper for callers that only need the mode string."""

    return search_decision(
        request,
        references,
        enabled=enabled,
        default_location=default_location,
    ).mode
