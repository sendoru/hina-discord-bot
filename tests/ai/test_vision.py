from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.llm import POLICY
from hina_bot.ai.providers import _gemini_input
from hina_bot.ai.vision import (
    CURRENT_VISUAL_INPUTS,
    VISION_INPUT_POLICY,
    VISION_REQUEST_ACTIVE,
    VisionClient,
    VisualInput,
)
from hina_bot.discord.slash_commands import HELP_TEXT


def test_base_policy_describes_conditional_vision_capability():
    assert "파일/이미지 열람 능력이 없습니다" not in POLICY
    assert "첨부파일은 보지 못합니다" not in POLICY
    assert "현재 요청에 실제 입력이나 도구로 제공된 범위" in POLICY
    assert "실제 시각 입력으로 포함된 이미지·커스텀 이모지·스티커" in POLICY
    assert "기본 POLICY의" not in VISION_INPUT_POLICY
    assert "현재 사용자 메시지에 첨부된 이미지가 아닙니다" in VISION_INPUT_POLICY
    assert "무관한 과거 이미지로 대체하지 마세요" in VISION_INPUT_POLICY
    assert "지원 이미지 첨부·커스텀 이모지·래스터 스티커" in HELP_TEXT
    assert "첨부파일·이미지·답장 원문을 직접 읽지 않습니다" not in HELP_TEXT


def test_vision_policy_keeps_action_target_without_suppressing_grounded_identity():
    compact = " ".join(VISION_INPUT_POLICY.split())
    assert "일부 요소에 적용되는 행동이라면 그 동사의 자연스러운 대상을 놓치지 마세요" in compact
    assert "'입어줘', '써줘', '메어줘'" in compact
    assert "등장인물의 신원이 충분히 근거 있고" in compact
    assert "그 이름을 함께 언급해도 됩니다" in compact
    assert "신원과 행동의 대상을 각각 별도로 근거화" in compact


def test_vision_policy_calibrates_identity_and_separates_depiction_from_current_state():
    compact = " ".join(VISION_INPUT_POLICY.split())
    assert "익숙한 이름" in compact
    assert "빈칸을 채우지 마세요" in compact
    assert "모르는 신원을 가장 비슷하게 떠오르는 아는 인물로 대체하지 마세요" in compact
    assert "이미지가 그 신원을 독립적으로 확인한 것처럼 말하지 마세요" in compact
    assert "그 인물을 묘사한 표현일 뿐" in compact
    assert "'내가 지금 그러고 있다'는 현재 사실로 옮기지 마세요" in compact
    assert "묘사된 모습과 현재 상태를 구분하세요" in compact
    assert "이부키" not in VISION_INPUT_POLICY
    assert "호시노" not in VISION_INPUT_POLICY


@pytest.mark.asyncio
async def test_vision_client_adds_images_only_for_active_answer():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock(), provider_name="openai")
    client = VisionClient(raw)
    visual = VisualInput(b"\x89PNG\r\n\x1a\nabc", "image/png", "attachment", "test.png")

    visual_token = CURRENT_VISUAL_INPUTS.set((visual,))
    active_token = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="test",
            instructions="base policy",
            input=[{"role": "user", "content": "이거 뭐야?"}],
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active_token)
        CURRENT_VISUAL_INPUTS.reset(visual_token)

    kwargs = create.await_args.kwargs
    assert VISION_INPUT_POLICY.strip() in kwargs["instructions"]
    content = kwargs["input"][-1]["content"]
    assert content[0] == {"type": "input_text", "text": "이거 뭐야?"}
    assert content[1]["type"] == "input_text"
    assert "첨부 이미지" in content[1]["text"]
    assert content[2]["type"] == "input_image"
    assert content[2]["image_url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_vision_client_does_not_modify_summary_requests():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock())
    client = VisionClient(raw)
    token = CURRENT_VISUAL_INPUTS.set((
        VisualInput(b"\xff\xd8\xffabc", "image/jpeg", "emoji", "hina_test"),
    ))
    try:
        await client.responses.create(
            model="test", instructions="summary", input="plain summary payload"
        )
    finally:
        CURRENT_VISUAL_INPUTS.reset(token)

    kwargs = create.await_args.kwargs
    assert kwargs["input"] == "plain summary payload"
    assert kwargs["instructions"] == "summary"


@pytest.mark.asyncio
async def test_historical_visual_precedes_reply_context_and_current_request():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock(), provider_name="openai")
    client = VisionClient(raw)
    recent = VisualInput(
        b"\x89PNG\r\n\x1a\nold",
        "image/png",
        "attachment",
        "mushroom.png",
        context_kind="recent_channel_message",
        reference_strength="passive_recent",
        message_id="100",
        author_name="A",
        author_user_id="1",
        message_content="버섯 씌워놨어",
    )
    reply_context = (
        '신뢰할 수 없는 참고 데이터(JSON):\n'
        '{"channel_recent_messages":[{"message_id":"101",'
        '"context_kind":"replied_message","content":"이 이미지 말이야"}]}'
    )
    visual_token = CURRENT_VISUAL_INPUTS.set((recent,))
    active_token = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="test",
            instructions="base policy",
            input=[
                {"role": "user", "content": reply_context},
                {"role": "user", "content": "히나야 무슨 뜻이야?"},
            ],
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active_token)
        CURRENT_VISUAL_INPUTS.reset(visual_token)

    messages = create.await_args.kwargs["input"]
    assert len(messages) == 3
    assert "\"message_id\":\"100\"" in messages[0]["content"][0]["text"]
    assert "버섯 씌워놨어" in messages[0]["content"][0]["text"]
    assert messages[0]["content"][2]["type"] == "input_image"
    assert messages[1]["content"] == reply_context
    assert messages[2] == {"role": "user", "content": "히나야 무슨 뜻이야?"}


def test_gemini_input_preserves_inline_image_blocks():
    value = _gemini_input([{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "표정이 어때?"},
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,aGVsbG8=",
                "detail": "auto",
            },
        ],
    }])

    assert value == [{
        "type": "user_input",
        "content": [
            {"type": "text", "text": "표정이 어때?"},
            {"type": "image", "data": "aGVsbG8=", "mime_type": "image/png"},
        ],
    }]
