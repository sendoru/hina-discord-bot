"""Deterministic model routing for long-term memory summarization."""

from __future__ import annotations

import json

from .model_routing import ModelPlan, ModelTier

_PERSONAL_TARGET_CHARS = 1800
_SHARED_TARGET_CHARS = 1200
_MEMORY_POLICY = "memory-v2"


def _linear_ramp(value: int, *, start: int, full: int, maximum: float) -> float:
    if value <= start:
        return 0.0
    if value >= full:
        return maximum
    return maximum * (value - start) / (full - start)


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
    pending_payload,
    *,
    shared: bool = False,
) -> ModelPlan:
    """Choose a tier from the actual compression load without semantic heuristics."""
    if settings.model_routing_mode != "adaptive":
        return fixed_memory_model_plan(settings)

    target_chars = _SHARED_TARGET_CHARS if shared else _PERSONAL_TARGET_CHARS
    previous_chars = len((previous_memory or "").strip())
    pending_chars = len(json.dumps(
        pending_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ))

    capacity_load = round(_linear_ramp(
        previous_chars,
        start=int(target_chars * 0.45),
        full=target_chars,
        maximum=2.0,
    ), 3)
    pending_load = round(_linear_ramp(
        pending_chars,
        start=800,
        full=5000,
        maximum=2.0,
    ), 3)

    components = tuple(
        (name, value)
        for name, value in (
            ("capacity_load", capacity_load),
            ("pending_load", pending_load),
        )
        if value > 0
    )
    score = round(capacity_load + pending_load, 3)
    threshold = settings.memory_routing_smart_threshold
    smart = score >= threshold
    reasons = tuple(name for name, _ in components) or ("routine_memory_update",)

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
        reasons=reasons,
        policy=_MEMORY_POLICY,
        components=components,
    )


__all__ = [
    "build_memory_model_plan",
    "fixed_memory_model_plan",
]
