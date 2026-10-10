from dataclasses import dataclass

from hina_bot.core.evidence_sufficiency import EvidenceAssessment

from .freshness import FreshnessMode, needs_location_clarification
from .information_routing import InformationRoute


@dataclass(frozen=True)
class SearchDecision:
    mode: str
    locked: bool
    reason: str


def search_decision(
    request,
    references,
    *,
    enabled,
    default_location="",
    local_evidence: EvidenceAssessment | None = None,
) -> SearchDecision:
    """Return the deterministic baseline plus whether semantic routing may override it.

    LOCAL_THEN_WEB no longer inspects selected reference wording. Its local/no-web decision
    requires a structured proposition-level EvidenceAssessment. Missing assessment fails
    closed to web lookup.
    """

    if not enabled:
        return SearchDecision("none", True, "disabled")
    if request.route in {
        InformationRoute.MEMORY, InformationRoute.CLOCK, InformationRoute.LOCAL_LORE,
    }:
        return SearchDecision("none", True, "local_only")
    if request.route == InformationRoute.WEB:
        if (
            request.freshness == FreshnessMode.REQUIRED
            and needs_location_clarification(request.lore_query)
            and not default_location
        ):
            # Do not let a semantic classifier force a location-dependent search without a location.
            return SearchDecision("auto", True, "missing_location")
        return SearchDecision("required", True, "deterministic_web")
    if request.route == InformationRoute.LOCAL_THEN_WEB:
        if local_evidence is None:
            return SearchDecision("required", True, "structured_evidence_missing")
        if not local_evidence.sufficient:
            return SearchDecision("required", True, local_evidence.reason)
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
