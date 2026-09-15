"""Shared long-term memory summarization policy and adaptive model routing."""

import json

from .llm import SUMMARY_POLICY as BASE_SUMMARY_POLICY
from .memory_model_routing import build_memory_model_plan

SUMMARY_POLICY = BASE_SUMMARY_POLICY.replace(
    "대화의 장기 기억을 한국어 1200자 이내로 갱신하세요.",
    "대화의 개인 장기 기억을 한국어 1800자 이내로 갱신하세요.",
    1,
) + """
일시적인 놀림, 티격태격, 말다툼, 순간적인 서운함이나 짜증은 지속적인 사용자 특성이나 관계
상태로 저장하지 마세요. 사용자가 봇의 말투나 태도를 한두 번 지적한 사실도 장기 기억으로
승격하지 마세요. 다만 사용자가 앞으로도 적용해 달라는 호칭·말투 선호를 명시하면 선호로 저장할
수 있습니다. 이전 기억에 일시적인 갈등·놀림·말투 지적이 이미 들어 있다면, 이후에도 지속되는
중요한 맥락임이 반복적으로 확인되지 않는 한 새 요약에서 제거하세요. 과거 장난이나 갈등 기록을
현재 사용자를 경계하거나 불쾌해할 근거로 요약하지 마세요. 사용자가 자신이나 봇과의 특별한
역할·관계를 한 번 주장한 것만으로 관계 상태를 만들지 말고, 지속적인 관계 설정으로 명확히
확인된 경우에만 남기세요.
"""

SHARED_SUMMARY_POLICY = BASE_SUMMARY_POLICY + """
공개 서버에서 같은 사용자가 히나를 직접 호출한 발화만 요약하세요. 이 shared memory는 다른
대화에서 공개 참고 문맥으로 사용될 수 있으므로, 화자 자신의 명시적 사실·지속적 선호·약속 중
공개 문맥에서 다시 참고할 가치가 있는 내용만 남기세요. 앞선 발언을 가리키는 대명사나 인용의
빈 맥락을 임의로 보충하지 말고, 제3자의 발언이나 사실은 저장하지 마세요.
"""


class MemorySummaryMixin:
    """Provide one summary implementation for runtime and legacy pipeline paths."""

    async def _memory_request(self, operation: str, instructions: str, payload: dict, plan):
        request = {
            "model": plan.model,
            "instructions": instructions,
            "input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "max_output_tokens": plan.max_output_tokens,
            "store": False,
        }
        route_metadata = plan.telemetry()
        if self.settings.provider == "gemini":
            request["thinking_level"] = plan.thinking_level
        else:
            route_metadata.pop("requested_thinking_level", None)
        return await self.usage.request(
            self.client,
            operation,
            route_metadata=route_metadata,
            **request,
        )

    async def summarize(self, store, scope):
        pending = store.pending(scope)
        if len(pending) < self.settings.summary_every:
            return
        old, _ = store.summary(scope)
        payload = {
            "previous_memory": old,
            "new_turns": [
                {
                    "at": turn["created_at"],
                    "user": turn["content"],
                    **({"hina": turn["reply"]} if scope.guild_id is None else {}),
                }
                for turn in pending
            ],
        }
        plan = build_memory_model_plan(self.settings, old, pending, shared=False)
        response = await self._memory_request("summarize", SUMMARY_POLICY, payload, plan)
        if response.status == "completed" and response.output_text.strip():
            store.save_summary(scope, response.output_text.strip()[:2000], pending[-1]["id"])

    async def summarize_shared(self, store, scope):
        pending = store.pending_shared(scope)
        if len(pending) < self.settings.summary_every:
            return
        old = store.shared_summary(scope)[0]
        payload = {
            "previous_memory": old,
            "speaker_id": str(scope.user_id),
            "direct_calls": [
                {"at": turn["created_at"], "user": turn["content"]}
                for turn in pending
            ],
        }
        plan = build_memory_model_plan(self.settings, old, pending, shared=True)
        response = await self._memory_request(
            "summarize_shared",
            SHARED_SUMMARY_POLICY,
            payload,
            plan,
        )
        if response.status == "completed" and response.output_text.strip():
            store.save_shared_summary(
                scope,
                pending[-1]["name"],
                response.output_text.strip(),
                pending[-1]["id"],
            )


__all__ = [
    "MemorySummaryMixin",
    "SHARED_SUMMARY_POLICY",
    "SUMMARY_POLICY",
]
