"""Deterministic per-turn model, reasoning, and generation-budget routing."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .information_plan import InformationPlan

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
_CHAT_POLICY = "chat-v4"
_HYBRID_POLICY = "chat-hybrid-v4"
_SEMANTIC_VALUES = {"": 0.0, "low": 0.0, "medium": 1.0, "high": 2.0}


def _linear_ramp(value: int, *, start: int, full: int, maximum: float) -> float:
    if value <= start:
        return 0.0
    if value >= full:
        return maximum
    return maximum * (value - start) / (full - start)


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
    return bool(pattern.search(negated.sub("", text)))


def _reference_chars(references: Sequence[object]) -> int:
    total = 0
    for row in references:
        if isinstance(row, Mapping):
            total += len(str(row.get("content", "")))
        else:
            total += len(str(row))
    return total


def semantic_value(level: str) -> float:
    return _SEMANTIC_VALUES.get(level, 0.0)


def local_semantic_level(information: InformationPlan) -> str:
    """Return only high-confidence local semantic hints used for cheap fallback/bypass."""
    visible = information.routing.visible_content.strip()
    prior = information.routing.prior_user_request.strip()
    if _affirmative_match(visible, _COMPLEX_TASK_REQUEST, _NEGATED_COMPLEX_TASK):
        return "high"
    if prior and _affirmative_match(prior, _COMPLEX_TASK_REQUEST, _NEGATED_COMPLEX_TASK):
        return "high"
    return ""


def wants_expanded_output(information: InformationPlan) -> bool:
    return _affirmative_match(
        information.routing.visible_content.strip(),
        _LONG_ANSWER_REQUEST,
        _NEGATED_LONG_ANSWER,
    )


def chat_objective_components(
    information: InformationPlan,
    *,
    context_chars: int = 0,
    visual_inputs: Sequence[object] = (),
) -> tuple[tuple[str, float], ...]:
    """Measure four physical loads without interpreting the request's semantic difficulty."""
    request_load = _soft_length_score(len(information.routing.visible_content.strip()))
    context_load = _linear_ramp(
        max(0, context_chars),
        start=1000,
        full=8000,
        maximum=2.0,
    )
    evidence_load = _linear_ramp(
        _reference_chars(information.references),
        start=500,
        full=5000,
        maximum=1.0,
    )
    if information.search_mode == "required":
        evidence_load += 0.5
    evidence_load = min(evidence_load, 1.5)
    visual_load = min(len(tuple(visual_inputs)) / 4.0, 1.0)
    return tuple(
        (name, round(value, 3))
        for name, value in (
            ("request_load", request_load),
            ("context_load", context_load),
            ("evidence_load", evidence_load),
            ("visual_load", visual_load),
        )
    )


def baseline_route_state(
    settings,
    information: InformationPlan,
    *,
    context_chars: int = 0,
    visual_inputs: Sequence[object] = (),
) -> tuple[float, str, str]:
    """Return deterministic score/tier plus the local semantic hint without making a ModelPlan."""
    objective = chat_objective_components(
        information,
        context_chars=context_chars,
        visual_inputs=visual_inputs,
    )
    local_level = local_semantic_level(information)
    score = round(sum(value for _, value in objective) + semantic_value(local_level), 3)
    tier = ModelTier.SMART.value if score >= settings.model_routing_smart_threshold else ModelTier.FAST.value
    return score, tier, local_level


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
    semantic_route_mode: str = "off"
    semantic_route_status: str = "not_used"
    semantic_route_level: str = ""
    semantic_route_codes: tuple[str, ...] = ()
    model_route_decision_source: str = "deterministic"
    model_route_baseline_tier: str = ""

    def telemetry(self) -> dict:
        telemetry = {
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
        if self.semantic_route_mode != "off":
            telemetry.update({
                "semantic_route_mode": self.semantic_route_mode,
                "semantic_route_status": self.semantic_route_status,
                "model_route_decision_source": self.model_route_decision_source,
                "model_route_baseline_tier": self.model_route_baseline_tier,
            })
            if self.semantic_route_level:
                telemetry["semantic_route_level"] = self.semantic_route_level
            if self.semantic_route_codes:
                telemetry["semantic_route_codes"] = list(self.semantic_route_codes)
        return telemetry


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
    context_chars: int = 0,
    visual_inputs: Sequence[object] = (),
    semantic_level: str = "",
    semantic_codes: Sequence[str] = (),
    semantic_route_mode: str = "off",
    semantic_route_status: str = "not_used",
    model_route_decision_source: str = "deterministic",
    model_route_baseline_tier: str = "",
) -> ModelPlan:
    """Choose the final tier from physical load plus one semantic difficulty score."""
    if settings.model_routing_mode != "adaptive":
        return fixed_model_plan(settings)

    objective = chat_objective_components(
        information,
        context_chars=context_chars,
        visual_inputs=visual_inputs,
    )
    local_level = local_semantic_level(information)
    chosen_level = semantic_level if semantic_value(semantic_level) >= semantic_value(local_level) else local_level
    semantic_score = semantic_value(chosen_level)
    components = objective + (("semantic_score", round(semantic_score, 3)),)
    score = round(sum(value for _, value in components), 3)
    threshold = settings.model_routing_smart_threshold
    smart = score >= threshold
    expanded_output = wants_expanded_output(information)

    reasons = [name for name, value in objective if value > 0]
    if semantic_score > 0:
        reasons.append("semantic_score")
    if expanded_output:
        reasons.append("long_answer_budget")
    if not reasons:
        reasons.append("routine_request")

    return ModelPlan(
        tier=ModelTier.SMART if smart else ModelTier.FAST,
        model=settings.smart_model if smart else settings.fast_model,
        max_output_tokens=(
            settings.smart_output_tokens
            if smart or expanded_output
            else settings.fast_output_tokens
        ),
        thinking_level=(
            settings.gemini_smart_thinking_level
            if smart else settings.gemini_fast_thinking_level
        ),
        score=score,
        smart_threshold=threshold,
        reasons=tuple(reasons),
        policy=_HYBRID_POLICY if semantic_route_mode != "off" else _CHAT_POLICY,
        components=components,
        semantic_route_mode=semantic_route_mode,
        semantic_route_status=semantic_route_status,
        semantic_route_level=chosen_level,
        semantic_route_codes=tuple(semantic_codes),
        model_route_decision_source=model_route_decision_source,
        model_route_baseline_tier=model_route_baseline_tier,
    )


__all__ = [
    "ModelPlan",
    "ModelTier",
    "baseline_route_state",
    "build_model_plan",
    "chat_objective_components",
    "fixed_model_plan",
    "local_semantic_level",
    "semantic_value",
    "wants_expanded_output",
]
