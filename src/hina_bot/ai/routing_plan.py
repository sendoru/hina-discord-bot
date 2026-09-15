"""Explicit per-turn routing plan shared by the production LLM pipeline."""

from dataclasses import dataclass

from .contextual_routing import (
    build_query,
    find_anchor,
    find_prior_user_request,
    is_followup,
)


@dataclass(frozen=True)
class RoutingPlan:
    """Separate the literal user turn from text used only for routing decisions."""

    visible_content: str
    routing_query: str
    anchor: str = ""
    anchor_source: str = ""
    prior_user_request: str = ""

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
    followup = is_followup(content)
    anchor = (
        find_anchor(store, scope, rows, use_memory=use_memory)
        if followup
        else None
    )
    prior_user_request = (
        find_prior_user_request(store, scope, rows, use_memory=use_memory)
        if followup
        else ""
    )
    if (
        anchor
        and anchor.source == "explicit_reply"
        and anchor.role != "assistant"
        and anchor.author_user_id != str(scope.user_id)
    ):
        # An explicit reply to another person's message changes the topic. Do not carry an older
        # request's complexity into that new thread merely because it was the caller's latest turn.
        prior_user_request = ""
    anchor_text = anchor.text if anchor else ""
    return RoutingPlan(
        visible_content=content,
        routing_query=build_query(content, anchor_text),
        anchor=anchor_text,
        anchor_source=anchor.source if anchor else "",
        prior_user_request=prior_user_request,
    )


__all__ = ["RoutingPlan", "build_routing_plan"]
