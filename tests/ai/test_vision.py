from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.providers import _gemini_input
from hina_bot.ai.reference_context_blocks import reference_context_blocks
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
    assert len(messages) == 2
    reference_blocks = messages[0]["content"]
    assert reference_blocks[0] == {"type": "input_text", "text": reply_context}
    assert "현재 사용자의 새 발화가 아니며" in reference_blocks[1]["text"]
    assert "\"message_id\":\"100\"" in reference_blocks[2]["text"]
    assert "버섯 씌워놨어" in reference_blocks[2]["text"]
    assert reference_blocks[4]["type"] == "input_image"
    assert messages[1] == {"role": "user", "content": "히나야 무슨 뜻이야?"}


@pytest.mark.asyncio
async def test_gemini_reply_image_is_reference_not_another_user_turn():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock(), provider_name="gemini")
    client = VisionClient(raw)
    reply_image = VisualInput(
        b"old image", "image/png", "attachment", "stamp.png",
        context_kind="replied_message", reference_strength="explicit_reply",
        message_id="100", author_name="another user", author_user_id="200",
        message_content="서류에 찍을 도장을 하나 샀는데 어때?",
    )
    current_emoji = VisualInput(
        b"new image", "image/png", "emoji", "reaction",
        context_kind="current_message", message_id="101",
        author_name="current user", author_user_id="300",
    )
    context = (
        '신뢰할 수 없는 참고 데이터(JSON):\\n'
        '{"active_reply_chain":[{"message_id":"100","author_user_id":"200",'
        '"content":"서류에 찍을 도장을 하나 샀는데 어때?",'
        '"context_kind":"replied_message"}]}'
    )
    token = CURRENT_VISUAL_INPUTS.set((reply_image, current_emoji))
    active = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="gemini-3.5-flash-lite",
            instructions="base policy",
            input=[
                {"role": "user", "content": context},
                {"role": "user", "content": "이거 어때?"},
            ],
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active)
        CURRENT_VISUAL_INPUTS.reset(token)

    messages = create.await_args.kwargs["input"]
    assert len(messages) == 2
    assert messages[0]["role"] == messages[1]["role"] == "user"
    reference = messages[0]["content"]
    assert reference[0]["text"] == context
    assert "별도의 새 발화" not in reference[0]["text"]
    assert '"author_user_id":"200"' in reference[2]["text"]
    assert "도장을 하나 샀는데" in reference[2]["text"]
    assert reference[4]["type"] == "input_image"
    assert reference[4]["resolution"] == "high"
    current = messages[1]["content"]
    assert current[0] == {"type": "input_text", "text": "이거 어때?"}
    assert current[2]["type"] == "input_image"
    assert current[2]["resolution"] == "low"

    gemini_steps = _gemini_input(messages)
    assert len(gemini_steps) == 2
    assert all(step["type"] == "user_input" for step in gemini_steps)
    assert gemini_steps[0]["content"][4]["type"] == "image"
    assert gemini_steps[1]["content"][-1]["type"] == "image"


@pytest.mark.asyncio
async def test_historical_visual_without_reference_envelope_keeps_current_text_last():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock(), provider_name="openai")
    client = VisionClient(raw)
    visual = VisualInput(
        b"image", "image/png", "attachment",
        context_kind="recent_channel_message", reference_strength="passive_recent",
        message_id="10", author_user_id="5",
    )
    token = CURRENT_VISUAL_INPUTS.set((visual,))
    active = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="test", instructions="base",
            input=[{"role": "user", "content": "그 이미지 어때?"}],
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active)
        CURRENT_VISUAL_INPUTS.reset(token)

    messages = create.await_args.kwargs["input"]
    assert len(messages) == 1
    assert messages[0]["content"][-1] == {
        "type": "input_text", "text": "그 이미지 어때?",
    }
    assert messages[0]["content"][-2]["type"] == "input_image"


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


@pytest.mark.asyncio
async def test_partitioned_reference_retains_historical_visual_in_reference_step():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock(), provider_name="gemini")
    client = VisionClient(raw)
    historical = VisualInput(
        b"old picture", "image/png", "attachment", "previous.png",
        context_kind="replied_message", reference_strength="explicit_reply",
        message_id="101", author_user_id="12", message_content="original message",
    )
    current = VisualInput(
        b"new picture", "image/png", "attachment", "current.png",
        context_kind="current_message", message_id="103",
    )
    reference = reference_context_blocks({
        "data_notice": "untrusted",
        "current_speaker": {"user_id": "4"},
        "active_reply_chain": [{"message_id": "101", "content": "original message"}],
    })
    visual_token = CURRENT_VISUAL_INPUTS.set((historical, current))
    active_token = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="gemini-3.5-flash-lite", instructions="policy",
            input=[
                {"role": "user", "content": reference},
                {"role": "user", "content": "what is this?"},
            ],
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active_token)
        CURRENT_VISUAL_INPUTS.reset(visual_token)

    messages = create.await_args.kwargs["input"]
    assert len(messages) == 2
    reference_parts = messages[0]["content"]
    assert reference_parts[:len(reference)] == reference
    assert any(
        block.get("type") == "input_image" for block in reference_parts[len(reference):]
    )
    assert messages[1]["content"][0]["text"] == "what is this?"
    assert messages[1]["content"][-1]["type"] == "input_image"
    converted = _gemini_input(messages)
    assert len(converted) == 2
    assert any(block["type"] == "image" for block in converted[0]["content"])
    assert any(block["type"] == "image" for block in converted[1]["content"])


@pytest.mark.asyncio
async def test_vision_reference_emoji_metadata_omits_id_without_touching_asset_uri():
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock(), provider_name="gemini")
    client = VisionClient(raw)
    raw_emoji = "<a:hello:1495269226938630315>"
    visual = VisualInput(
        b"GIF89a123", "image/gif", "emoji", "hello",
        context_kind="replied_message", reference_strength="explicit_reply",
        message_id="100", author_user_id="200",
        message_content="이거 " + raw_emoji,
        uri="https://cdn.discordapp.com/emojis/1495269226938630315.gif",
    )
    token = CURRENT_VISUAL_INPUTS.set((visual,))
    active = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="gemini-3.5-flash-lite", instructions="base",
            input=[
                {"role": "user", "content": "신뢰할 수 없는 참고 데이터(JSON):\\n{}"},
                {"role": "user", "content": "이거 어때?"},
            ],
        )
    finally:
        CURRENT_VISUAL_INPUTS.reset(token)
        VISION_REQUEST_ACTIVE.reset(active)

    messages = create.await_args.kwargs["input"]
    reference = messages[0]["content"]
    assert '"message_content":"이거 :hello:"' in reference[2]["text"]
    assert raw_emoji not in reference[2]["text"]
    assert "1495269226938630315" in reference[4]["image_url"]
    assert reference[3]["text"].startswith("명시적 답장 대상 메시지의")
    assert not reference[3]["text"].startswith("[")
    assert not reference[3]["text"].endswith("]")
    assert visual.message_content == "이거 " + raw_emoji


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context_kind", "reference_strength", "expect_reference"),
    [
        ("current_message", "current_message", True),
        ("replied_message", "explicit_reply", True),
        ("reply_origin_source", "prior_explicit_reply", True),
        ("recent_channel_message", "passive_recent", False),
    ],
)
async def test_hina_reference_is_separate_from_discord_visuals(
    tmp_path, context_kind, reference_strength, expect_reference,
):
    reference = tmp_path / "hina.webp"
    reference.write_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"test")
    create = AsyncMock(return_value=NS(status="completed"))
    raw = NS(responses=NS(create=create), close=AsyncMock(), provider_name="gemini")
    client = VisionClient(raw, reference_image_path=str(reference))
    original_messages = [
        {"role": "user", "content": "신뢰할 수 없는 참고 데이터(JSON):\n{}"},
        {"role": "user", "content": "이게 누구야?"},
    ]
    visual = VisualInput(
        b"\x89PNG\r\n\x1a\nother",
        "image/png", "attachment", "other.png",
        context_kind=context_kind, reference_strength=reference_strength,
        message_id="123",
    )
    token = CURRENT_VISUAL_INPUTS.set((visual,))
    active = VISION_REQUEST_ACTIVE.set(True)
    try:
        await client.responses.create(
            model="gemini-3.5-flash-lite", instructions="existing",
            input=original_messages,
        )
    finally:
        VISION_REQUEST_ACTIVE.reset(active)
        CURRENT_VISUAL_INPUTS.reset(token)

    messages = create.await_args.kwargs["input"]
    assert len(messages) == 2  # Never invent a new Discord conversation turn.
    assert original_messages[0]["content"].startswith("신뢰할 수 없는")
    assert isinstance(original_messages[1]["content"], str)  # No in-place mutation.
    first = messages[0]["content"]
    if expect_reference:
        assert "개발자가 제공한" in first[0]["text"]
        assert first[1]["image_url"].startswith("data:image/webp;base64,")
        assert first[1]["resolution"] == "medium"
        assert sum(
            block.get("type") == "input_image"
            for message in messages
            for block in (message["content"] if isinstance(message["content"], list) else [])
        ) == 2
        converted = _gemini_input(messages)
        assert converted[0]["content"][1]["type"] == "image"
        assert converted[0]["content"][1]["resolution"] == "medium"
    else:
        assert first[0]["text"].startswith("신뢰할 수 없는")
        assert "개발자가 제공한" not in str(messages)
        assert sum(
            block.get("type") == "input_image"
            for message in messages
            for block in (message["content"] if isinstance(message["content"], list) else [])
        ) == 1


@pytest.mark.asyncio
async def test_hina_reference_does_not_change_other_providers_or_inactive_requests(tmp_path):
    reference = tmp_path / "hina.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nref")
    for provider, active_request in (
        ("openai", True),
        ("gemini", False),
    ):
        create = AsyncMock(return_value=NS(status="completed"))
        client = VisionClient(
            NS(responses=NS(create=create), provider_name=provider),
            reference_image_path=str(reference),
        )
        token = CURRENT_VISUAL_INPUTS.set((
            VisualInput(b"actual", "image/png", "attachment"),
        ))
        active = VISION_REQUEST_ACTIVE.set(active_request)
        try:
            await client.responses.create(
                model="gemini-3.5-flash", instructions="existing",
                input=[{"role": "user", "content": "질문"}],
            )
        finally:
            VISION_REQUEST_ACTIVE.reset(active)
            CURRENT_VISUAL_INPUTS.reset(token)
        assert "개발자가 제공한" not in str(create.await_args.kwargs["input"])


@pytest.mark.asyncio
async def test_missing_or_invalid_hina_reference_falls_back_to_existing_vision(tmp_path):
    for image_path in (tmp_path / "missing.png", tmp_path / "invalid.png"):
        if image_path.name == "invalid.png":
            image_path.write_bytes(b"not an image")
        create = AsyncMock(return_value=NS(status="completed"))
        client = VisionClient(
            NS(responses=NS(create=create), provider_name="gemini"),
            reference_image_path=str(image_path),
        )
        token = CURRENT_VISUAL_INPUTS.set((
            VisualInput(b"real", "image/png", "attachment"),
        ))
        active = VISION_REQUEST_ACTIVE.set(True)
        try:
            await client.responses.create(
                model="gemini-3.5-flash", instructions="existing",
                input=[{"role": "user", "content": "이게 누구야?"}],
            )
        finally:
            VISION_REQUEST_ACTIVE.reset(active)
            CURRENT_VISUAL_INPUTS.reset(token)
        content = create.await_args.kwargs["input"][0]["content"]
        assert sum(item["type"] == "input_image" for item in content) == 1
        assert "개발자가 제공한" not in str(content)
