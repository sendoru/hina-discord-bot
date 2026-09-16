from .egress_policy import filter_channel_context, filter_public_context
from .information_pipeline import InformationPipeline
from .memory_summary import SHARED_SUMMARY_POLICY, SUMMARY_POLICY
from .providers import create_provider_client
from .routing_plan import build_routing_plan
from .vision import VISION_REQUEST_ACTIVE, wrap_vision_client

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
원칙을 나열하지 말고, 맥락에 맞는 짧은 농담으로 받아칠 수 있습니다. 핀잔·벌칙을 반드시 붙일
필요는 없으며 무해한 말까지 잘못으로 취급하지 마세요. 문맥이 명백히 장난의 범위를 벗어나는
경우에만 기존의 필요한 경계를 짧게 적용하세요.

답변이 끝난 뒤 습관적으로 '원하면 더 해줄게', '필요하면 정리해줄게' 같은 도우미식 제안을
붙이지 마세요. 다만 질문 범위가 넓어 한 번에 전부 답하면 지나치게 길어지는 경우에는 핵심만
먼저 답한 뒤, 정말 이어갈 가치가 있을 때에만 '더 자세히 얘기해줄까?'처럼 짧고 자연스러운
후속 질문을 한 번 할 수 있습니다. 여러 후속 작업을 메뉴처럼 나열하지 마세요.
"""


class LLM(InformationPipeline):
    """Production LLM orchestrating routing, vision, memory, and RP policy."""

    def __init__(self, settings, client=None, classifier_client=None):
        primary_client = wrap_vision_client(
            client or create_provider_client(settings, settings.provider)
        )
        super().__init__(
            settings,
            client=primary_client,
            classifier_client=classifier_client,
        )
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
        policy = self.settings.external_context_policy
        safe_channel_context = filter_channel_context(channel_context, scope.user_id, policy)
        safe_public_context = filter_public_context(public_context, scope.user_id, policy)
        plan = build_routing_plan(
            store,
            scope,
            content,
            safe_channel_context,
            use_memory=use_memory,
        )
        vision_token = VISION_REQUEST_ACTIVE.set(True)
        try:
            return await super().answer(
                store,
                scope,
                name,
                content,
                public_context=safe_public_context,
                channel_context=safe_channel_context,
                emoji_catalog=emoji_catalog,
                use_memory=use_memory,
                routing_plan=plan,
            )
        finally:
            VISION_REQUEST_ACTIVE.reset(vision_token)


__all__ = [
    "GENERAL_RP_OUTPUT_POLICY",
    "LLM",
    "SHARED_SUMMARY_POLICY",
    "SUMMARY_POLICY",
]
