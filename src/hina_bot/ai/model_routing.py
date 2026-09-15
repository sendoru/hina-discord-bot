"""Deterministic per-turn model, reasoning, and generation-budget routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .information_plan import InformationPlan
from .information_routing import InformationRoute

_COMPLEX_REQUEST = re.compile(
    r"(?:분석|비교|검토|평가|설계|구현|리팩터|디버깅|증명|유도|알고리즘|"
    r"시간\s*복잡도|공간\s*복잡도|아키텍처|코드|원인.{0,12}(?:찾|분석)|"
    r"trade[ -]?off|장단점)",
    re.IGNORECASE,
)
_LONG_ANSWER_REQUEST = re.compile(
    r"(?:자세히|구체적으로|깊이\s*있게|차근차근|단계별|빠짐없이|"
    r"긴\s*(?:답변|글)|보고서|튜토리얼|가이드|전체(?:적으로)?\s*정리)",
    re.IGNORECASE,
)
_LISTED_REQUIREMENT = re.compile(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+", re.MULTILINE)
_SURROUNDING_CONTEXT_KINDS = frozenset({"speaker_thread", "prior_reply_source"})


def _linear_ramp(value: int, *, start: int, full: int, maximum: float) -> float:
    """Grow linearly after start and saturate at maximum once full is reached."""
    if value <= start:
        return 0.0
    if value >= full:
        return maximum
    return maximum * (value - start) / (full - start)


def _saturating_count(value: int, *, maximum: float, half: float) -> float:
    """Give early items more weight while keeping large counts bounded."""
    if value <= 0:
        return 0.0
    return maximum * value / (value + half)


class ModelTier(StrEnum):
    FIXED = "fixed"
    FAST = "fast"
    SMART = "smart"


@dataclass(frozen=True)
class ModelPlan:
    tier: ModelTier
    model: str
    max_output_tokens: int
    thinking_level: str
    score: float
    smart_threshold: float
    reasons: tuple[str, ...]

    def telemetry(self) -> dict:
        return {
            "model_tier": self.tier.value,
            "model_route_score": self.score,
            "model_route_threshold": self.smart_threshold,
            "model_route_reasons": list(self.reasons),
            "requested_max_output_tokens": self.max_output_tokens,
            "requested_thinking_level": self.thinking_level,
        }


def fixed_model_plan(settings) -> ModelPlan:
    return ModelPlan(
        tier=ModelTier.FIXED,
        model=settings.model,
        max_output_tokens=settings.output_tokens,
        thinking_level=settings.gemini_thinking_level,
        score=0.0,
        smart_threshold=settings.model_routing_smart_threshold,
        reasons=("fixed_mode",),
    )


def build_model_plan(
    settings,
    information: InformationPlan,
    *,
    channel_context: list[dict] | tuple[dict, ...] = (),
    visual_count: int = 0,
) -> ModelPlan:
    """Choose a tier without an additional model call or inspecting hidden model output."""
    if settings.model_routing_mode != "adaptive":
        return fixed_model_plan(settings)

    visible_text = information.routing.visible_content.strip()
    anchor_text = information.routing.anchor.strip()
    score = 0.0
    reasons = []

    def add(points: float, reason: str) -> None:
        nonlocal score
        if points <= 0:
            return
        score += points
        reasons.append(reason)

    # Strong semantic signals come from the user's literal request. A quoted/anchored source may be
    # technically complex, but source wording such as "분석" must not be mistaken for an instruction.
    if _COMPLEX_REQUEST.search(visible_text):
        add(2.0, "complex_request")
    elif anchor_text and _COMPLEX_REQUEST.search(anchor_text):
        add(0.5, "complex_reference")

    if _LONG_ANSWER_REQUEST.search(visible_text):
        add(2.0, "long_answer_requested")

    # Very long literal requests can justify the smart tier on their own, but the ramp is deliberately
    # broad so ordinary medium-sized prompts do not jump tiers near an arbitrary boundary.
    add(
        _linear_ramp(len(visible_text), start=500, full=2500, maximum=2.0),
        "input_length",
    )

    requirement_count = max(
        visible_text.count("?"),
        len(_LISTED_REQUIREMENT.findall(visible_text)),
    )
    add(
        _saturating_count(
            max(0, requirement_count - 1), maximum=1.0, half=1.5
        ),
        "multiple_requirements",
    )

    # One visual or one web lookup is useful context, not a reason by itself to pay for the smart tier.
    add(
        _saturating_count(visual_count, maximum=1.25, half=1.5),
        "visual_input",
    )

    if information.search_mode == "required":
        add(0.5, "required_web_search")
    if information.route == InformationRoute.LOCAL_THEN_WEB:
        add(0.75, "multi_source_lore")

    # One reference is ordinary grounding. Additional references add synthesis work with diminishing
    # returns so retrieval volume cannot dominate the decision by itself.
    add(
        _saturating_count(
            max(0, len(information.references) - 1), maximum=0.8, half=3.0
        ),
        "reference_volume",
    )

    reply_chars = sum(
        len(str(row.get("content", "")))
        for row in channel_context
        if row.get("context_kind") == "replied_message"
    )
    # Explicit replies are stronger than ambient history because the user deliberately selected the
    # source. Several thousand characters of quoted material can therefore reach smart by itself.
    add(
        _linear_ramp(reply_chars, start=300, full=2000, maximum=2.0),
        "explicit_reply_length",
    )

    target_rows = [
        row for row in channel_context
        if row.get("context_kind") == "target_user_history"
    ]
    if any(row.get("target_retrieval_mode") == "deep" for row in target_rows):
        add(1.6, "deep_target_history")
    elif target_rows:
        add(0.4, "basic_target_history")

    target_chars = sum(len(str(row.get("content", ""))) for row in target_rows)
    add(
        _linear_ramp(target_chars, start=300, full=2400, maximum=0.5),
        "target_history_volume",
    )

    # Keep explicit replies and target history out of ambient-context scoring so the same text is not
    # counted twice. Ambient context should influence routing, but only as a modest supporting signal.
    surrounding_chars = sum(
        len(str(row.get("content", "")))
        for row in channel_context
        if row.get("context_kind") in _SURROUNDING_CONTEXT_KINDS
    )
    add(
        _linear_ramp(surrounding_chars, start=1500, full=5000, maximum=0.75),
        "surrounding_context_length",
    )

    score = round(score, 3)
    smart_threshold = settings.model_routing_smart_threshold
    smart = score >= smart_threshold
    if not reasons:
        reasons.append("routine_request")
    return ModelPlan(
        tier=ModelTier.SMART if smart else ModelTier.FAST,
        model=settings.smart_model if smart else settings.fast_model,
        max_output_tokens=(
            settings.smart_output_tokens if smart else settings.fast_output_tokens
        ),
        thinking_level=(
            settings.gemini_smart_thinking_level
            if smart else settings.gemini_fast_thinking_level
        ),
        score=score,
        smart_threshold=smart_threshold,
        reasons=tuple(reasons),
    )


__all__ = ["ModelPlan", "ModelTier", "build_model_plan", "fixed_model_plan"]
