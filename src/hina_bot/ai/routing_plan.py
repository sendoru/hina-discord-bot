"""Explicit per-turn routing plan shared by the production LLM pipeline."""

from dataclasses import dataclass

from .contextual_routing import (
    build_query,
    find_anchor,
    find_prior_user_request,
    is_followup,
)
from .egress_policy import BOT_INTERACTIONS_ONLY, filter_channel_context

_CLASSIFIER_CONTEXT_BUDGET = 4000
_CLASSIFIER_CAUSAL_BUDGET = 2800
_CLASSIFIER_CONTEXT_ITEMS = 8
_CLASSIFIER_CAUSAL_KINDS = frozenset({
    "prior_reply_source",
    "reply_origin_source",
    "reply_origin_request",
    "replied_message",
})


@dataclass(frozen=True)
class ClassifierContextItem:
    """Small, provenance-aware context item safe to serialize to the routing classifier."""

    kind: str
    role: str
    ownership: str
    text: str


@dataclass(frozen=True)
class RoutingPlan:
    """Separate the literal user turn from text used only for routing decisions."""

    visible_content: str
    routing_query: str
    anchor: str = ""
    anchor_source: str = ""
    prior_user_request: str = ""
    classifier_anchor: str = ""
    classifier_context: tuple[ClassifierContextItem, ...] = ()

    @property
    def expanded(self) -> bool:
        return self.routing_query != self.visible_content


def _ownership(row: dict, current_user_id: int | str) -> str:
    if str(row.get("role") or "") == "assistant":
        return "assistant"
    author = str(row.get("author_user_id") or row.get("user_id") or "")
    return "self" if author == str(current_user_id) else "external"


def _take_context(
    rows: list[dict],
    *,
    current_user_id: int | str,
    budget: int,
    slots: int,
) -> tuple[list[ClassifierContextItem], int, int]:
    """Take newest useful rows within a text budget, then restore chronological order."""
    selected: list[ClassifierContextItem] = []
    remaining = max(0, budget)
    slots = max(0, slots)
    for row in reversed(rows):
        if remaining <= 0 or len(selected) >= slots:
            break
        text = str(row.get("content") or "").strip()
        if not text:
            continue
        text = text[-remaining:]
        selected.append(ClassifierContextItem(
            kind=str(row.get("context_kind") or "context"),
            role=str(row.get("role") or ""),
            ownership=_ownership(row, current_user_id),
            text=text,
        ))
        remaining -= len(text)
    selected.reverse()
    return selected, remaining, slots - len(selected)


def _classifier_context(
    rows: list[dict],
    *,
    current_user_id: int | str,
    policy: str,
) -> tuple[ClassifierContextItem, ...]:
    """Select causal reply context plus a small same-speaker window for semantic routing."""
    safe = filter_channel_context(rows, current_user_id, policy)
    causal = [
        row for row in safe
        if str(row.get("context_kind") or "") in _CLASSIFIER_CAUSAL_KINDS
    ]
    speaker = [
        row for row in safe
        if str(row.get("context_kind") or "") == "speaker_thread"
    ]

    causal_budget = (
        _CLASSIFIER_CAUSAL_BUDGET if speaker else _CLASSIFIER_CONTEXT_BUDGET
    )
    causal_selected, causal_unused, slots = _take_context(
        causal,
        current_user_id=current_user_id,
        budget=causal_budget,
        slots=min(4, _CLASSIFIER_CONTEXT_ITEMS),
    )
    speaker_budget = _CLASSIFIER_CONTEXT_BUDGET - (causal_budget - causal_unused)
    speaker_selected, _, _ = _take_context(
        speaker,
        current_user_id=current_user_id,
        budget=speaker_budget,
        slots=slots,
    )

    # Same-speaker continuity precedes the currently selected reply chain conceptually.
    return tuple(speaker_selected + causal_selected)


def build_routing_plan(
    store,
    scope,
    content: str,
    channel_context: list[dict] | None = None,
    *,
    use_memory: bool = True,
    classifier_context_policy: str = BOT_INTERACTIONS_ONLY,
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
    classifier_anchor = ""
    if (
        anchor
        and anchor.source == "explicit_reply"
        and (
            anchor.role == "assistant"
            or (
                anchor.role == "user"
                and anchor.author_user_id == str(scope.user_id)
            )
        )
    ):
        classifier_anchor = anchor_text

    classifier_context = _classifier_context(
        rows,
        current_user_id=scope.user_id,
        policy=classifier_context_policy,
    )
    return RoutingPlan(
        visible_content=content,
        routing_query=build_query(content, anchor_text),
        anchor=anchor_text,
        anchor_source=anchor.source if anchor else "",
        prior_user_request=prior_user_request,
        classifier_anchor=classifier_anchor,
        classifier_context=classifier_context,
    )


__all__ = ["ClassifierContextItem", "RoutingPlan", "build_routing_plan"]
