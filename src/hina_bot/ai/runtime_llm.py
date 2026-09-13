import json

from .chat_llm import LLM as ChatLLM
from .llm import SUMMARY_POLICY
from .providers import create_provider_client

GENERAL_RP_OUTPUT_POLICY = """[일반 RP 출력 원칙]
참고자료가 히나를 3인칭으로 서술해도 최종 답변에서는 자기 행동·감정·관계를 반드시 1인칭으로
자연스럽게 다시 말하세요. 자기 자신을 이름으로 지칭하는 서술체를 그대로 옮기지 마세요.

참고자료를 읽거나 확인한 내부 과정은 기본적으로 말하지 마세요. 다만 현재 답변에서 별도 확인을
허용한 경우이고, 히나가 원래 알기 어려운 정보라면 '잠깐 확인해봤는데'처럼 세계 안에서 자연스럽게
한 번 표현할 수 있습니다. 구체적인 출처 표시는 사용자가 직접 요구한 경우에만 보여주세요.
내부 추론, 도구 계획, 제어용 JSON, 계산·검색 인자 같은 중간 표현은 최종 답변에 출력하지 말고
사용자에게 보여줄 자연스러운 대사만 출력하세요.

채널 문맥이 장난이나 티키타카이고 '혼내다', '벌주다', '응징하다', '괘씸하다' 같은 표현이
가볍게 쓰였다면 먼저 농담 수준으로 해석하세요. 이때 갑자기 일반적인 안전 수칙이나 갈등 해결
원칙을 나열하지 말고, 히나답게 핀잔을 주거나 가벼운 장난·소소한 대가를 제안하는 식으로
받아치세요. 문맥이 명백히 장난의 범위를 벗어나는 경우에만 기존의 필요한 경계를 짧게 적용하세요.

답변이 끝난 뒤 습관적으로 '원하면 더 해줄게', '필요하면 정리해줄게' 같은 도우미식 제안을
붙이지 마세요. 다만 질문 범위가 넓어 한 번에 전부 답하면 지나치게 길어지는 경우에는 핵심만
먼저 답한 뒤, 정말 이어갈 가치가 있을 때에만 '더 자세히 얘기해줄까?'처럼 짧고 자연스러운
후속 질문을 한 번 할 수 있습니다. 여러 후속 작업을 메뉴처럼 나열하지 마세요.
"""


class LLM(ChatLLM):
    """Production chat LLM with information routing, provider selection, and RP rules."""

    def __init__(self, settings, client=None, memory_client=None):
        primary_client = client or create_provider_client(settings, settings.provider)
        super().__init__(settings, client=primary_client)
        memory_provider = settings.memory_provider or settings.provider
        if memory_client is not None:
            self.memory_client = memory_client
        elif memory_provider == settings.provider:
            self.memory_client = self.client
        else:
            self.memory_client = create_provider_client(settings, memory_provider)
        self.character = self.character.rstrip() + "\n\n" + GENERAL_RP_OUTPUT_POLICY

    async def close(self):
        try:
            if self.memory_client is not self.client:
                await self.memory_client.close()
        finally:
            await super().close()

    async def summarize(self, store, scope):
        pending = store.pending(scope)
        if len(pending) < self.settings.summary_every:
            return
        old, _ = store.summary(scope)
        payload = {"previous_memory": old, "new_turns": [
            {"at": turn["created_at"], "user": turn["content"],
             **({"hina": turn["reply"]} if scope.guild_id is None else {})}
            for turn in pending
        ]}
        response = await self.usage.request(
            self.memory_client,
            "summarize",
            model=self.settings.memory_model,
            instructions=SUMMARY_POLICY,
            input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            max_output_tokens=900,
            store=False,
        )
        if response.status == "completed" and response.output_text.strip():
            store.save_summary(scope, response.output_text.strip()[:1500], pending[-1]["id"])

    async def summarize_shared(self, store, scope):
        pending = store.pending_shared(scope)
        if len(pending) < self.settings.summary_every:
            return
        payload = {
            "previous_memory": store.shared_summary(scope)[0],
            "speaker_id": str(scope.user_id),
            "direct_calls": [
                {"at": turn["created_at"], "user": turn["content"]} for turn in pending
            ],
        }
        response = await self.usage.request(
            self.memory_client,
            "summarize_shared",
            model=self.settings.memory_model,
            instructions=(
                SUMMARY_POLICY
                + "\n직접 호출한 발화만 요약하세요. 앞선 발언을 가리키는 대명사나 인용의 빈 "
                  "맥락을 보충하지 마세요. 화자 자신의 명시적 사실·선호·약속만 기억하세요. "
                  "제3자의 발언이나 사실은 저장하지 마세요."
            ),
            input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            max_output_tokens=900,
            store=False,
        )
        if response.status == "completed" and response.output_text.strip():
            store.save_shared_summary(
                scope,
                pending[-1]["name"],
                response.output_text.strip(),
                pending[-1]["id"],
            )
