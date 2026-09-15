"""Deterministic model routing for long-term memory summarization."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .model_routing import ModelPlan, ModelTier

_PERSONAL_TARGET_CHARS = 1800
_SHARED_TARGET_CHARS = 1200
_SHARED_SCORE_DISCOUNT = 0.35
_MEMORY_POLICY = "memory-v1"
_MEMORY_UPDATE_SIGNAL = re.compile(
    r"(?:앞으로(?:는|도)?|이제부터|정정|(?:설정|선호|호칭|말투).{0,12}(?:바꿔|바꿨|변경)|"
    r"(?:기억|메모).{0,12}(?:말고|지워|삭제|잊어)|더\s*이상.{0,16}(?:말고|하지|안\s*해)|"
    r"(?:취소했|취소할게|바꿨어|바꿨고|변경했|변경할게)|"
    r"(?:아니고|아니라).{0,24}(?:야|이야|라고\s*해|로\s*해))",
    re.IGNORECASE,
)


def _linear_ramp(value: int, *, start: int, full: int, maximum: float) -> float:
    if value <= start:
        return 0.0
    if value >= full:
        return maximum
    return maximum * (value - start) / (full - start)


def _saturating_count(value: int, *, maximum: float, half: float) -> float:
    if value <= 0:
        return 0.0
    return maximum * value / (value + half)


def _text_field(row, key: str) -> str:
    """Read dict/sqlite3.Row-like values without requiring a .get() method."""
    try:
        value = row[key]
    except (IndexError, KeyError, TypeError):
        return ""
    return "" if value is None else str(value)


def fixed_memory_model_plan(settings) -> ModelPlan:
    return ModelPlan(
        tier=ModelTier.FIXED,
        model=settings.model,
        max_output_tokens=settings.memory_output_tokens,
        thinking_level=settings.gemini_thinking_level,
        score=0.0,
        smart_threshold=settings.memory_routing_smart_threshold,
        reasons=("fixed_mode",),
        policy="memory-fixed-v1",
        components=(),
    )


def build_memory_model_plan(
    settings,
    previous_memory: str,
    pending: Sequence,
    *,
    include_replies: bool,
    shared: bool = False,
) -> ModelPlan:
    """Choose a fast/smart tier for memory compaction without an extra model call."""
    if settings.model_routing_mode != "adaptive":
        return fixed_memory_model_plan(settings)

    target_chars = _SHARED_TARGET_CHARS if shared else _PERSONAL_TARGET_CHARS
    previous_chars = len((previous_memory or "").strip())
    pending_chars = 0
    user_text_parts = []
    for turn in pending:
        content = _text_field(turn, "content")
        reply = _text_field(turn, "reply") if include_replies else ""
        pending_chars += len(content) + len(reply)
        if content:
            user_text_parts.append(content)

    reasons: list[str] = []
    components: list[tuple[str, float]] = []

    def add(points: float, reason: str) -> None:
        points = round(points, 3)
        if points <= 0:
            return
        components.append((reason, points))
        reasons.append(reason)

    add(
        _linear_ramp(
            previous_chars,
            start=int(target_chars * 0.45),
            full=target_chars,
            maximum=1.15,
        ),
        "memory_capacity_pressure",
    )
    add(
        _linear_ramp(pending_chars, start=800, full=5000, maximum=1.15),
        "pending_input_volume",
    )

    update_count = len(_MEMORY_UPDATE_SIGNAL.findall("\n".join(user_text_parts)))
    add(
        _saturating_count(update_count, maximum=1.2, half=1.0),
        "memory_update_signal",
    )

    if previous_chars >= int(target_chars * 0.7) and pending_chars >= 1500:
        add(0.9, "compaction_pressure")

    extra_pending = max(0, len(pending) - settings.summary_every)
    add(
        _saturating_count(extra_pending, maximum=0.6, half=4.0),
        "extra_pending_turns",
    )

    subtotal = sum(points for _, points in components)
    if shared and subtotal > 0:
        discount = round(min(subtotal, _SHARED_SCORE_DISCOUNT), 3)
        components.append(("shared_scope_discount", -discount))
        reasons.append("shared_scope_discount")

    score = round(sum(points for _, points in components), 3)
    threshold = settings.memory_routing_smart_threshold
    smart = score >= threshold
    if not reasons:
        reasons.append("routine_memory_update")

    return ModelPlan(
        tier=ModelTier.SMART if smart else ModelTier.FAST,
        model=settings.smart_model if smart else settings.fast_model,
        max_output_tokens=settings.memory_output_tokens,
        thinking_level=(
            settings.gemini_smart_thinking_level
            if smart
            else settings.gemini_fast_thinking_level
        ),
        score=score,
        smart_threshold=threshold,
        reasons=tuple(reasons),
        policy=_MEMORY_POLICY,
        components=tuple(components),
    )


__all__ = [
    "build_memory_model_plan",
    "fixed_memory_model_plan",
]
