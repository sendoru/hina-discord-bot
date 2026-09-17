"""Explicit per-turn routing plan shared by the production LLM pipeline."""

from dataclasses import dataclass

from .contextual_routing import (
    build_query,
    find_anchor,
    find_prior_user_request,
    is_followup,
    needs_context_grounding,
)
from .egress_policy import BOT_INTERACTIONS_ONLY, filter_channel_context

_CLASSIFIER_CONTEXT_BUDGET = 4000
_CLASSIFIER_CAUSAL_BUDGET = 2800
_CLASSIFIER_CONTEXT_ITEMS = 8
_CLASSIFIER_CROSS_SPEAKER_BUDGET = 1200
_CLASSIFIER_CROSS_SPEAKER_ITEMS = 4
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
) -> tuple[list[ClassifierContextItem], int]:
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
    return selected, remaining


def _cross_speaker_bot_interactions(
    rows: list[dict],
    *,
    current_user_id: int | str,
) -> list[dict]:
    """Return up to two recent external-user <-> assistant exchanges from ambient context.

    General channel chatter is deliberately excluded even under the ``full`` egress policy.  A user
    row must have direct-trigger provenance; assistant rows must identify an external reply target.
    Pairing keeps the classifier from seeing a bare answer when the matching bot-directed question is
    still present in the context window.
    """
    current = str(current_user_id)
    pending: list[tuple[int, str, dict]] = []
    exchanges: list[list[tuple[int, dict]]] = []

    for position, row in enumerate(rows):
        if str(row.get("context_kind") or "") != "channel_ambient":
            continue
        role = str(row.get("role") or "")
        if role == "user":
            author = str(row.get("author_user_id") or row.get("user_id") or "")
            if author == current or row.get("direct_trigger") is not True:
                continue
            pending.append((position, author, row))
            continue
        if role != "assistant":
            continue

        target = str(row.get("reply_target_user_id") or "")
        if not target or target == current:
            continue
        matched = None
        for pending_index in range(len(pending) - 1, -1, -1):
            if pending[pending_index][1] == target:
                matched = pending.pop(pending_index)
                break
        exchange: list[tuple[int, dict]] = []
        if matched is not None:
            exchange.append((matched[0], matched[2]))
        exchange.append((position, row))
        exchanges.append(exchange)

    # A just-triggered external question can be useful even if its bot reply has not entered the
    # recent-message buffer yet.
    exchanges.extend([[(position, row)] for position, _author, row in pending])
    exchanges.sort(key=lambda group: max(position for position, _row in group))

    selected: list[tuple[int, dict]] = []
    for group in exchanges[-2:]:
        selected.extend(group)
    selected.sort(key=lambda item: item[0])

    result = []
    seen = set()
    for _position, row in selected:
        message_id = str(row.get("message_id") or "")
        dedupe_key = message_id or id(row)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        item = dict(row)
        item["context_kind"] = "cross_speaker_bot_interaction"
        result.append(item)
    return result


def _classifier_context(
    rows: list[dict],
    *,
    current_user_id: int | str,
    policy: str,
    include_cross_speaker: bool = False,
) -> tuple[ClassifierContextItem, ...]:
    """Select causal, same-speaker, and conditionally cross-speaker classifier context."""
    safe = filter_channel_context(rows, current_user_id, policy)
    causal = [
        row for row in safe
        if str(row.get("context_kind") or "") in _CLASSIFIER_CAUSAL_KINDS
    ]
    speaker = [
        row for row in safe
        if str(row.get("context_kind") or "") == "speaker_thread"
    ]
    cross_speaker = (
        _cross_speaker_bot_interactions(safe, current_user_id=current_user_id)
        if include_cross_speaker
        else []
    )

    causal_budget = (
        _CLASSIFIER_CAUSAL_BUDGET
        if speaker or cross_speaker
        else _CLASSIFIER_CONTEXT_BUDGET
    )
    causal_selected, causal_unused = _take_context(
        causal,
        current_user_id=current_user_id,
        budget=causal_budget,
        slots=min(4, _CLASSIFIER_CONTEXT_ITEMS),
    )

    remaining_budget = _CLASSIFIER_CONTEXT_BUDGET - (causal_budget - causal_unused)
    remaining_slots = _CLASSIFIER_CONTEXT_ITEMS - len(causal_selected)
    cross_reserve = (
        min(_CLASSIFIER_CROSS_SPEAKER_BUDGET, remaining_budget)
        if cross_speaker
        else 0
    )
    cross_slot_reserve = min(2, remaining_slots) if cross_speaker else 0

    speaker_selected, speaker_unused = _take_context(
        speaker,
        current_user_id=current_user_id,
        budget=max(0, remaining_budget - cross_reserve),
        slots=max(0, remaining_slots - cross_slot_reserve),
    )
    remaining_budget = cross_reserve + speaker_unused
    remaining_slots -= len(speaker_selected)

    cross_selected, _ = _take_context(
        cross_speaker,
        current_user_id=current_user_id,
        budget=remaining_budget,
        slots=min(_CLASSIFIER_CROSS_SPEAKER_ITEMS, remaining_slots),
    )

    # Same-speaker continuity comes first conceptually, then the optional shared bot conversation,
    # while explicit reply/provenance context remains closest to the current request.
    return tuple(speaker_selected + cross_selected + causal_selected)


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

    has_explicit_reply = any(
        str(row.get("context_kind") or "") == "replied_message"
        for row in rows
    )
    grounding_needed = needs_context_grounding(
        content,
        has_explicit_reply=has_explicit_reply,
    )
    classifier_context = _classifier_context(
        rows,
        current_user_id=scope.user_id,
        policy=classifier_context_policy,
        # An explicit reply already has a higher-quality causal lane.  Only sample the shared bot
        # conversation when the request refers backwards without selecting a concrete message.
        include_cross_speaker=grounding_needed and not has_explicit_reply,
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
