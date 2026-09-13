from .freshness import FreshnessMode, needs_location_clarification
from .information_routing import InformationRoute

_RELATION_TERMS = (
    "만났", "만난", "만남", "대면", "대화", "친분", "관계", "접점",
    "방문", "출입", "들어갔", "들어간", "참여", "동행", "목격", "대치", "사건",
)


def enough_local(request, references):
    facts = [row for row in references if row.get("kind") == "world_fact"
             and row.get("awareness") not in {"audience_only", "inference", "unknown"}]
    if not facts:
        return False
    if not request.relation_or_event:
        return True
    return any(any(term in (row.get("reference", "") + " " + row.get("content", ""))
                   for term in _RELATION_TERMS) for row in facts)


def search_mode(request, references, *, enabled, default_location=""):
    if not enabled or request.route in {
        InformationRoute.MEMORY, InformationRoute.CLOCK, InformationRoute.LOCAL_LORE,
    }:
        return "none"
    if request.explicit_source:
        return "required"
    if request.route == InformationRoute.WEB:
        if (request.freshness == FreshnessMode.REQUIRED
                and needs_location_clarification(request.lore_query)
                and not default_location):
            return "auto"
        return "required"
    if request.route == InformationRoute.LOCAL_THEN_WEB:
        return "none" if enough_local(request, references) else "required"
    return "auto" if request.freshness == FreshnessMode.AUTO else "none"
