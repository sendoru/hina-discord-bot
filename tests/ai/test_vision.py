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
    assert "지원 이미지 첨부·커스텀 이모지·래스터 스티커" in HELP_TEXT
    assert "첨부파일·이미지·답장 원문을 직접 읽지 않습니다" not in HELP_TEXT


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
