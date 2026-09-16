"""Deterministic per-turn model, reasoning, and generation-budget routing."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .information_plan import InformationPlan
from .information_routing import InformationRoute

_COMPLEX_TASK_REQUEST = re.compile(
    r"(?:"
    r"(?:분석|비교|검토|평가|설계|구현|리팩터링?|디버깅|증명|유도)"
    r"(?:해|하(?:고|기|는|면|여|자|죠|세요|십시오)|해\s*(?:줘|주세요|줄래))|"
    r"(?:고쳐|수정해|개선해|해결해|최적화해)|"
    r"(?:코드|함수|클래스|테스트|쿼리|SQL|정규식).{0,12}"
    r"(?:작성해|짜\s*줘|구현해)|"
    r"(?:원인|문제점|개선(?:할\s*)?(?:점|부분)|병목|취약점|오류|버그)"
    r".{0,24}(?:찾아|찾아봐|분석해|검토해|고쳐|수정해|개선해|해결해)|"
    r"(?:analy[sz]e|compare|review|design|implement|refactor|debug|prove|fix|improve)"
    r"(?:\s+(?:this|it|the|my|our|please)|\b)"
    r")",
    re.IGNORECASE,
)
_NEGATED_COMPLEX_TASK = re.compile(
    r"(?:분석|비교|검토|평가|설계|구현|리팩터링?|디버깅|증명|유도|"
    r"수정|개선|해결|최적화).{0,8}(?:하지\s*말|하지는\s*말|말고|빼고)",
    re.IGNORECASE,
)
_LONG_ANSWER_REQUEST = re.compile(
    r"(?:"
    r"(?:자세히|구체적으로|깊이\s*있게|차근차근|단계별(?:로)?|빠짐없이)"
    r".{0,20}(?:설명|알려|정리|써|작성|답해)|"
    r"(?:긴\s*(?:답변|글)|보고서|튜토리얼|가이드).{0,16}(?:써|작성|만들|정리)|"
    r"전체(?:적으로)?\s*.{0,12}정리"
    r")",
    re.IGNORECASE,
)
_NEGATED_LONG_ANSWER = re.compile(
    r"(?:자세히|구체적으로|깊이\s*있게|차근차근|단계별(?:로)?|빠짐없이|"
    r"긴\s*(?:답변|글)|보고서|튜토리얼|가이드).{0,10}"
    r"(?:말하지\s*말|설명하지\s*말|하지\s*말|말고|빼고)",
    re.IGNORECASE,
)
_LISTED_REQUIREMENT = re.compile(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+", re.MULTILINE)
_SURROUNDING_CONTEXT_KINDS = frozenset({"speaker_thread", "prior_reply_source"})
_CHAT_POLICY = "chat-v3"
_VISUAL_SOURCE_UNITS = {
    "attachment": 1.0,
    "sticker": 0.5,
    "emoji": 0.25,
}
_STRONG_VISUAL_REFERENCES = frozenset({"current_message", "explicit_reply"})


def _linear_ramp(value: int, *, start: int, full: int, maximum: float) -> float:
    """Grow linearly after start and saturate at maximum once full is reached."""
    if value <= start:
        return 0.0
    if value >= full:
        return maximum
    return maximum * (value - start) / (full - start)


def _saturating_count(value: float, *, maximum: float, half: float) -> float:
    """Give early items more weight while keeping large counts bounded."""
    if value <= 0:
        return 0.0
    return maximum * value / (value + half)


def _soft_length_score(
    value: int,
    *,
    midpoint: int = 1500,
    full: int = 4000,
    maximum: float = 2.0,
) -> float:
    """Grow smoothly from zero, then give diminishing weight to very long input."""
    value = min(max(value, 0), full)
    if value == 0:
        return 0.0
    raw = value ** 2 / (value ** 2 + midpoint ** 2)
    full_raw = full ** 2 / (full ** 2 + midpoint ** 2)
    return maximum * raw / full_raw


def _affirmative_match(text: str, pattern: re.Pattern, negated: re.Pattern) -> bool:
    """Match a requested action after removing nearby explicit negative instructions."""
    return bool(pattern.search(negated.sub("", text)))


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
    policy: str
    components: tuple[tuple[str, float], ...]

    def telemetry(self) -> dict:
        return {
            "model_tier": self.tier.value,
            "model_route_score": self.score,
            "model_route_threshold": self.smart_threshold,
            "model_route_margin": round(self.score - self.smart_threshold, 3),
            "model_route_reasons": list(self.reasons),
            "model_route_policy": self.policy,
            "model_route_components": dict(self.components),
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
        policy="chat-fixed-v1",
        components=(),
    )


def build_model_plan(
    settings,
    information: InformationPlan,
    *,
    channel_context: list[dict] | tuple[dict, ...] = (),
    visual_inputs: Sequence[object] = (),
) -> ModelPlan:
    """Choose a tier without an additional model call or inspecting hidden model output."""
    if settings.model_routing_mode != "adaptive":
        return fixed_model_plan(settings)

    visible_text = information.routing.visible_content.strip()
    anchor_text = information.routing.anchor.strip()
    prior_user_request = information.routing.prior_user_request.strip()
    reasons: list[str] = []
    components: list[tuple[str, float]] = []

    def add(points: float, reason: str, *, minimum_reason: float = 0.0) -> None:
        points = round(points, 3)
        if points <= 0:
            return
        components.append((reason, points))
        if points >= minimum_reason:
            reasons.append(reason)

    # Strong semantic signals come from the user's literal request. A quoted/anchored source may be
    # technically complex, but source wording such as "분석" must not be mistaken for an instruction.
    if _affirmative_match(
        visible_text, _COMPLEX_TASK_REQUEST, _NEGATED_COMPLEX_TASK
    ):
        add(2.0, "complex_request")
    elif prior_user_request and _affirmative_match(
        prior_user_request, _COMPLEX_TASK_REQUEST, _NEGATED_COMPLEX_TASK
    ):
        add(2.0, "complex_followup")
    elif anchor_text and _affirmative_match(
        anchor_text, _COMPLEX_TASK_REQUEST, _NEGATED_COMPLEX_TASK
    ):
        add(0.5, "complex_reference")

    if _affirmative_match(
        visible_text, _LONG_ANSWER_REQUEST, _NEGATED_LONG_ANSWER
    ):
        add(2.0, "long_answer_requested")

    # Length is a weak signal for short input, becomes useful in the middle, and has diminishing
    # marginal weight for very long input. There is no hard boundary around an ordinary message size.
    add(
        _soft_length_score(len(visible_text)),
        "input_length",
        minimum_reason=0.05,
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

    strong_visual_units = sum(
        _VISUAL_SOURCE_UNITS.get(str(getattr(visual, "source", "")), 0.0)
        for visual in visual_inputs
        if getattr(visual, "reference_strength", "") in _STRONG_VISUAL_REFERENCES
    )
    passive_visual_units = sum(
        _VISUAL_SOURCE_UNITS.get(str(getattr(visual, "source", "")), 0.0)
        for visual in visual_inputs
        if getattr(visual, "reference_strength", "") == "passive_recent"
    )
    # Current-message and explicit-reply visuals are deliberate input. Passive recent images are
    # only weak continuity context and stay bounded well below a tier decision by themselves.
    add(
        _saturating_count(strong_visual_units, maximum=1.25, half=1.5),
        "strong_visual_input",
    )
    add(
        _saturating_count(passive_visual_units, maximum=0.3, half=2.0),
        "passive_visual_context",
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

    score = round(sum(points for _, points in components), 3)
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
        policy=_CHAT_POLICY,
        components=tuple(components),
    )


__all__ = ["ModelPlan", "ModelTier", "build_model_plan", "fixed_model_plan"]
