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
_RELEVANT_CONTEXT_KINDS = frozenset({
    "speaker_thread", "replied_message", "prior_reply_source", "target_user_history",
})


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
    score: int
    reasons: tuple[str, ...]

    def telemetry(self) -> dict:
        return {
            "model_tier": self.tier.value,
            "model_route_score": self.score,
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
        score=0,
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

    text = information.routing.routing_query.strip()
    score = 0
    reasons = []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    if _COMPLEX_REQUEST.search(text):
        add(2, "complex_request")
    if _LONG_ANSWER_REQUEST.search(text):
        add(2, "long_answer_requested")
    if len(text) >= 600:
        add(2, "long_input")
    if text.count("?") >= 2 or len(_LISTED_REQUIREMENT.findall(text)) >= 2:
        add(1, "multiple_requirements")
    if visual_count:
        add(1, "visual_input")
    if information.search_mode == "required":
        add(1, "required_web_search")
    if information.route == InformationRoute.LOCAL_THEN_WEB:
        add(1, "multi_source_lore")
    if len(information.references) >= 4:
        add(1, "many_references")

    reply_chars = sum(
        len(str(row.get("content", "")))
        for row in channel_context
        if row.get("context_kind") == "replied_message"
    )
    if reply_chars >= 1500:
        add(2, "long_explicit_reply")
    elif reply_chars >= 500:
        add(1, "substantial_explicit_reply")

    target_rows = [
        row for row in channel_context
        if row.get("context_kind") == "target_user_history"
    ]
    if any(row.get("target_retrieval_mode") == "deep" for row in target_rows):
        add(2, "deep_target_history")
    elif target_rows:
        add(1, "basic_target_history")

    relevant_chars = sum(
        len(str(row.get("content", "")))
        for row in channel_context
        if row.get("context_kind") in _RELEVANT_CONTEXT_KINDS
    )
    if relevant_chars >= 3000:
        add(1, "large_relevant_context")

    smart = score >= 2
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
        reasons=tuple(reasons),
    )


__all__ = ["ModelPlan", "ModelTier", "build_model_plan", "fixed_model_plan"]
