import json
from contextvars import ContextVar

from .chat_llm import LLM as ChatLLM
from .contextual_routing import build_query, find_anchor, is_followup
from .llm import SUMMARY_POLICY as BASE_SUMMARY_POLICY
from .providers import create_provider_client
from .vision import VISION_REQUEST_ACTIVE, wrap_vision_client

SUMMARY_POLICY = BASE_SUMMARY_POLICY + """
일시적인 놀림, 티격태격, 말다툼, 순간적인 서운함이나 짜증은 지속적인 사용자 특성이나 관계
상태로 저장하지 마세요. 사용자가 봇의 말투나 태도를 한두 번 지적한 사실도 장기 기억으로
승격하지 마세요. 다만 사용자가 앞으로도 적용해 달라는 호칭·말투 선호를 명시하면 선호로 저장할
수 있습니다. 이전 기억에 일시적인 갈등·놀림·말투 지적이 이미 들어 있다면, 이후에도 지속되는
중요한 맥락임이 반복적으로 확인되지 않는 한 새 요약에서 제거하세요. 과거 장난이나 갈등 기록을
현재 사용자를 경계하거나 불쾌해할 근거로 요약하지 마세요. 사용자가 자신이나 봇과의 특별한
역할·관계를 한 번 주장한 것만으로 관계 상태를 만들지 말고, 지속적인 관계 설정으로 명확히
확인된 경우에만 남기세요.
"""

GENERAL_RP_OUTPUT_POLICY = """[일반 RP 출력 원칙]
참고자료가 히나를 3인칭으로 서술해도 최종 답변에서는 자기 행동·감정·관계를 반드시 1인칭으로
자연스럽게 다시 말하세요. 자기 자신을 이름으로 지칭하는 서술체를 그대로 옮기지 마세요.
Discord 최종 답변에는 사용자가 실제로 읽을 대사와 필요한 정보만 출력하세요. '*한숨을 쉰다*',
'(고개를 젓는다)', '차분하게 바라본다' 같은 행동·표정·감정·장면 서술이나 무대 지시는 쓰지
마세요. 감정과 태도는 대사 자체의 어휘와 말투로만 드러내세요.

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

_VISIBLE_CONTENT = ContextVar("hina_visible_followup_content", default=None)


class _ResponsesProxy:
    """Restore the literal user turn after routing used an expanded follow-up query."""

    def __init__(self, responses):
        self._responses = responses

    async def create(self, **kwargs):
        visible = _VISIBLE_CONTENT.get()
        items = kwargs.get("input")
        if visible is not None and isinstance(items, list) and items:
            tail = items[-1]
            if isinstance(tail, dict) and tail.get("role") == "user":
                rewritten = list(items)
                rewritten_tail = dict(tail)
                rewritten_tail["content"] = visible
                rewritten[-1] = rewritten_tail
                kwargs = dict(kwargs)
                kwargs["input"] = rewritten
        return await self._responses.create(**kwargs)


class _ClientProxy:
    def __init__(self, client):
        self._client = client
        self.responses = _ResponsesProxy(client.responses)

    def __getattr__(self, name):
        return getattr(self._client, name)


class LLM(ChatLLM):
    """Production LLM: follow-up routing, information routing, vision, memory, and RP policy."""

    def __init__(self, settings, client=None, memory_client=None):
        primary_client = wrap_vision_client(
            client or create_provider_client(settings, settings.provider)
        )
        super().__init__(settings, client=primary_client)
        if not isinstance(self.client, _ClientProxy):
            self.client = _ClientProxy(self.client)

        memory_provider = settings.memory_provider or settings.provider
        if memory_client is not None:
            self.memory_client = memory_client
        elif memory_provider == settings.provider:
            # The vision facade/proxy is safe for summaries because answer() alone sets routing
            # and vision request state.
            self.memory_client = self.client
        else:
            self.memory_client = create_provider_client(settings, memory_provider)
        self.character = self.character.rstrip() + "\n\n" + GENERAL_RP_OUTPUT_POLICY

    async def answer(
        self,
        store,
        scope,
        name: str,
        content: str,
        public_context: list | None = None,
        channel_context: list | None = None,
        emoji_catalog: list | None = None,
        use_memory: bool = True,
    ) -> str:
        rows = channel_context or []
        anchor = (
            find_anchor(store, scope, rows, use_memory=use_memory)
            if is_followup(content)
            else ""
        )
        routing_query = build_query(content, anchor)
        visible_token = (
            _VISIBLE_CONTENT.set(content)
            if routing_query != content
            else None
        )
        vision_token = VISION_REQUEST_ACTIVE.set(True)
        try:
            return await super().answer(
                store,
                scope,
                name,
                routing_query,
                public_context=public_context,
                channel_context=channel_context,
                emoji_catalog=emoji_catalog,
                use_memory=use_memory,
            )
        finally:
            VISION_REQUEST_ACTIVE.reset(vision_token)
            if visible_token is not None:
                _VISIBLE_CONTENT.reset(visible_token)

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


__all__ = ["LLM", "GENERAL_RP_OUTPUT_POLICY", "SUMMARY_POLICY"]
