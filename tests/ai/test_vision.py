from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.providers import _gemini_input
from hina_bot.ai.vision import (
    CURRENT_VISUAL_INPUTS,
    VISION_INPUT_POLICY,
    VISION_REQUEST_ACTIVE,
    VisionClient,
    VisualInput,
)


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
async def test_vision_client_keeps_visual_only_turn_textless():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock(), provider_name="openai")
    client = VisionClient(raw)
    visual = VisualInput(b"image", "image/png", "sticker", "hina")

    visual_token = CURRENT_VISUAL_INPUTS.set((visual,))
    active_token = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="test",
            instructions="base policy",
            input=[{"role": "user", "content": ""}],
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active_token)
        CURRENT_VISUAL_INPUTS.reset(visual_token)

    content = create.await_args.kwargs["input"][-1]["content"]
    assert content[0]["type"] == "input_text"
    assert "스티커" in content[0]["text"]
    assert content[1]["type"] == "input_image"
    assert all(
        block.get("text") != "이 이미지를 봐줘."
        for block in content
        if block.get("type") == "input_text"
    )


@pytest.mark.asyncio
async def test_vision_client_prefers_validated_remote_uri_over_inline_base64():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(
        responses=NS(create=create),
        close=AsyncMock(),
        provider_name="gemini",
    )
    client = VisionClient(raw)
    visual = VisualInput(
        b"\x89PNG\r\n\x1a\nabc",
        "image/png",
        "attachment",
        "test.png",
        uri="https://cdn.discordapp.com/attachments/1/2/test.png?ex=signed",
    )

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

    content = create.await_args.kwargs["input"][-1]["content"]
    assert content[2]["image_url"].startswith("https://cdn.discordapp.com/")
    assert "base64" not in content[2]["image_url"]


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


def test_visual_media_resolution_tracks_source_and_reference_strength():
    assert VisualInput(
        b"image", "image/png", "attachment",
        reference_strength="current_message",
    ).media_resolution() == "high"
    assert VisualInput(
        b"image", "image/png", "attachment",
        reference_strength="explicit_reply",
    ).media_resolution() == "high"
    assert VisualInput(
        b"image", "image/png", "attachment",
        reference_strength="same_speaker",
    ).media_resolution() == "medium"
    assert VisualInput(
        b"image", "image/png", "emoji",
        reference_strength="current_message",
    ).media_resolution() == "low"
    assert VisualInput(
        b"image", "image/png", "sticker",
        reference_strength="explicit_reply",
    ).media_resolution() == "low"


@pytest.mark.asyncio
async def test_gemini_3_vision_request_adds_per_image_resolution():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(
        responses=NS(create=create),
        close=AsyncMock(),
        provider_name="gemini",
    )
    client = VisionClient(raw)
    visual = VisualInput(
        b"GIF89a123",
        "image/gif",
        "emoji",
        "hina_test",
        uri="https://cdn.discordapp.com/emojis/123.gif",
    )

    visual_token = CURRENT_VISUAL_INPUTS.set((visual,))
    active_token = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="gemini-3.8-flash",
            instructions="base policy",
            input=[{"role": "user", "content": "이 이모지 뭐야?"}],
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active_token)
        CURRENT_VISUAL_INPUTS.reset(visual_token)

    image = create.await_args.kwargs["input"][-1]["content"][2]
    assert image["resolution"] == "low"


@pytest.mark.asyncio
async def test_non_gemini_3_vision_request_keeps_existing_image_shape():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(
        responses=NS(create=create),
        close=AsyncMock(),
        provider_name="openai",
    )
    client = VisionClient(raw)
    visual = VisualInput(b"image", "image/png", "attachment")

    visual_token = CURRENT_VISUAL_INPUTS.set((visual,))
    active_token = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="gpt-5.6",
            instructions="base policy",
            input=[{"role": "user", "content": "이거 뭐야?"}],
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active_token)
        CURRENT_VISUAL_INPUTS.reset(visual_token)

    image = create.await_args.kwargs["input"][-1]["content"][2]
    assert "resolution" not in image


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


def test_gemini_input_forwards_image_resolution():
    value = _gemini_input([{
        "role": "user",
        "content": [{
            "type": "input_image",
            "image_url": "https://cdn.discordapp.com/attachments/1/2/test.png",
            "resolution": "medium",
        }],
    }])

    assert value == [{
        "type": "user_input",
        "content": [{
            "type": "image",
            "uri": "https://cdn.discordapp.com/attachments/1/2/test.png",
            "mime_type": "image/png",
            "resolution": "medium",
        }],
    }]
