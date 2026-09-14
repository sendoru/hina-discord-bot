"""Explicit per-turn routing plan shared by the production LLM pipeline."""

from dataclasses import dataclass

from .contextual_routing import build_query, find_anchor, is_followup


@dataclass(frozen=True)
class RoutingPlan:
    """Separate the literal user turn from text used only for routing decisions."""

    visible_content: str
    routing_query: str
    anchor: str = ""

    @property
    def expanded(self) -> bool:
        return self.routing_query != self.visible_content


def build_routing_plan(
    store,
    scope,
    content: str,
    channel_context: list[dict] | None = None,
    *,
    use_memory: bool = True,
) -> RoutingPlan:
    rows = channel_context or []
    anchor = (
        find_anchor(store, scope, rows, use_memory=use_memory)
        if is_followup(content)
        else ""
    )
    return RoutingPlan(
        visible_content=content,
        routing_query=build_query(content, anchor),
        anchor=anchor,
    )


__all__ = ["RoutingPlan", "build_routing_plan"]
