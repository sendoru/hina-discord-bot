from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS
from hina_bot.core.config import Settings
from hina_bot.core.store import Store
from hina_bot.discord.vision import (
    VisionLimits,
    collect_visual_inputs,
    message_has_visual,
    visual_context_refs,
)
from hina_bot.discord.web_bot import HinaClient

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16
GIF = b"GIF89a" + b"x" * 16


def _history(messages):
    async def rows():
        for message in messages:
            yield message

    return rows()


def test_message_has_visual_uses_structural_metadata_without_reading_bytes():
    image = NS(
        size=len(PNG),
        content_type="image/png",
        filename="screen.png",
        read=AsyncMock(return_value=PNG),
    )
    message = NS(content="", attachments=[image], stickers=[])

    assert message_has_visual(message) is True
    image.read.assert_not_awaited()


def test_visual_context_refs_follow_selected_message_provenance():
    refs = visual_context_refs([
        {
            "message_id": "10",
            "content": "자 여깄어",
            "has_visual": True,
            "context_kind": "speaker_thread",
            "reference_strength": "same_speaker",
            "provenance_class": "conversation",
            "author_user_id": "100",
            "at": "2026-09-24T09:38:07+00:00",
        },
        {
            "message_id": "11",
            "content": "텍스트만",
            "context_kind": "speaker_thread",
        },
    ])

    assert len(refs) == 1
    assert refs[0].message_id == "10"
    assert refs[0].context_kind == "speaker_thread"
    assert refs[0].reference_strength == "same_speaker"
    assert refs[0].author_user_id == "100"


@pytest.mark.asyncio
async def test_collects_image_attachment_and_ignores_non_image_attachment():
    image = NS(
        size=len(PNG),
        content_type="image/png",
        filename="screen.png",
        read=AsyncMock(return_value=PNG),
    )
    text = NS(
        size=20,
        content_type="text/plain",
        filename="note.txt",
        read=AsyncMock(return_value=b"not an image"),
    )
    message = NS(content="히나야 이거 봐", attachments=[image, text], stickers=[])

    visuals = await collect_visual_inputs(message)

    assert len(visuals) == 1
    assert visuals[0].source == "attachment"
    assert visuals[0].name == "screen.png"
    assert visuals[0].mime_type == "image/png"
    assert visuals[0].context_kind == "current_message"
    assert visuals[0].reference_strength == "current_message"
    text.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_collects_custom_emoji_once_and_raster_sticker():
    urls = []

    async def downloader(url):
        urls.append(url)
        return GIF if "/emojis/123." in url else PNG

    sticker = NS(
        name="히나 스티커",
        url="https://cdn.discordapp.com/stickers/456.png",
        format=NS(name="png"),
    )
    message = NS(
        content="히나야 <:hina_test:123> <:hina_test:123>",
        attachments=[],
        stickers=[sticker],
    )

    visuals = await collect_visual_inputs(message, downloader=downloader)

    assert [(v.source, v.name) for v in visuals] == [
        ("emoji", "hina_test"),
        ("sticker", "히나 스티커"),
    ]
    assert len([url for url in urls if "/emojis/123." in url]) == 1
    assert visuals[0].mime_type == "image/gif"
    assert visuals[1].mime_type == "image/png"


@pytest.mark.asyncio
async def test_source_quotas_are_shared_across_message_contexts():
    current_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="current.png",
        read=AsyncMock(return_value=PNG),
    )
    reply_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="reply.png",
        read=AsyncMock(return_value=PNG),
    )
    channel = NS(id=10)
    target = NS(
        id=40,
        content="",
        author=NS(id=200, display_name="대상", name="대상"),
        channel=channel,
        attachments=[reply_attachment],
        stickers=[],
    )
    message = NS(
        id=41,
        content="히나야 이거 봐",
        author=NS(id=100, display_name="사용자", name="사용자"),
        channel=channel,
        reference=NS(message_id=40, channel_id=10, resolved=target),
        attachments=[current_attachment],
        stickers=[],
    )

    visuals = await collect_visual_inputs(
        message,
        limits=VisionLimits(attachments=1, emojis=0, stickers=0),
        include_reply=True,
    )

    assert [(v.name, v.context_kind) for v in visuals] == [
        ("current.png", "current_message")
    ]
    reply_attachment.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_quotas_are_independent_and_skip_extra_downloads():
    attachments = [
        NS(
            size=len(PNG),
            content_type="image/png",
            filename=f"screen-{index}.png",
            read=AsyncMock(return_value=PNG),
        )
        for index in range(3)
    ]
    stickers = [
        NS(
            name=f"sticker-{index}",
            url=f"https://cdn.discordapp.com/stickers/{100 + index}.png",
            format=NS(name="png"),
        )
        for index in range(3)
    ]
    content = "히나야 " + " ".join(
        f"<:emoji_{index}:{200 + index}>" for index in range(5)
    )
    message = NS(content=content, attachments=attachments, stickers=stickers)
    urls = []

    async def downloader(url):
        urls.append(url)
        return PNG

    visuals = await collect_visual_inputs(
        message,
        limits=VisionLimits(attachments=1, emojis=3, stickers=2),
        downloader=downloader,
    )

    assert [v.source for v in visuals] == [
        "attachment",
        "emoji",
        "emoji",
        "emoji",
        "sticker",
        "sticker",
    ]
    assert attachments[0].read.await_count == 1
    attachments[1].read.assert_not_awaited()
    attachments[2].read.assert_not_awaited()
    assert len([url for url in urls if "/emojis/" in url]) == 3
    assert len([url for url in urls if "/stickers/" in url]) == 2


def test_vision_limits_follow_runtime_settings():
    limits = VisionLimits.from_settings(Settings(
        "test",
        "test",
        vision_max_attachments=2,
        vision_max_emojis=15,
        vision_max_stickers=5,
    ))

    assert limits == VisionLimits(attachments=2, emojis=15, stickers=5)


@pytest.mark.asyncio
async def test_lottie_sticker_is_not_sent_as_image():
    sticker = NS(
        name="animated",
        url="https://cdn.discordapp.com/stickers/789.json",
        format=NS(name="lottie"),
    )
    downloader = AsyncMock(return_value=PNG)
    message = NS(content="히나야", attachments=[], stickers=[sticker])

    assert await collect_visual_inputs(message, downloader=downloader) == []
    downloader.assert_not_awaited()


@pytest.mark.asyncio
async def test_collects_explicit_reply_visual_with_provenance():
    attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="older.png",
        read=AsyncMock(return_value=PNG),
    )
    channel = NS(id=10)
    target = NS(
        id=70,
        content="이거",
        author=NS(id=200, display_name="민수", name="민수"),
        channel=channel,
        attachments=[attachment],
        stickers=[],
    )
    message = NS(
        id=71,
        content="히나야 이건 뭐야?",
        author=NS(id=100, display_name="사용자", name="사용자"),
        channel=channel,
        reference=NS(message_id=70, channel_id=10, resolved=target),
        attachments=[],
        stickers=[],
    )

    visuals = await collect_visual_inputs(message, include_reply=True)

    assert len(visuals) == 1
    visual = visuals[0]
    assert visual.context_kind == "replied_message"
    assert visual.reference_strength == "explicit_reply"
    assert visual.message_id == "70"
    assert visual.author_name == "민수"
    assert "명시적 답장 대상 메시지" in visual.label(1)
    assert "강한 참조" in visual.label(1)


@pytest.mark.asyncio
async def test_strict_author_filter_blocks_other_users_reply_visual():
    attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="other.png",
        read=AsyncMock(return_value=PNG),
    )
    channel = NS(id=10)
    target = NS(
        id=80,
        content="",
        author=NS(id=200, display_name="다른 사용자", name="다른 사용자"),
        channel=channel,
        attachments=[attachment],
        stickers=[],
    )
    message = NS(
        id=81,
        content="히나야",
        author=NS(id=100, display_name="사용자", name="사용자"),
        channel=channel,
        reference=NS(message_id=80, channel_id=10, resolved=target),
        attachments=[],
        stickers=[],
    )

    visuals = await collect_visual_inputs(
        message,
        include_reply=True,
        allowed_context_author_id=100,
    )

    assert visuals == []
    attachment.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_collects_bounded_recent_image_messages_and_ignores_passive_emojis():
    newest_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="newest.png",
        read=AsyncMock(return_value=PNG),
    )
    older_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="older.png",
        read=AsyncMock(return_value=PNG),
    )
    author = NS(id=100, display_name="사용자", name="사용자", bot=False)
    newest = NS(
        id=95,
        content="사진",
        author=author,
        webhook_id=None,
        attachments=[newest_attachment],
        stickers=[],
    )
    emoji_only = NS(
        id=94,
        content="<:old_emoji:123>",
        author=author,
        webhook_id=None,
        attachments=[],
        stickers=[],
    )
    older = NS(
        id=93,
        content="예전 사진",
        author=author,
        webhook_id=None,
        attachments=[older_attachment],
        stickers=[],
    )
    channel = NS(id=10, history=lambda **kwargs: _history([newest, emoji_only, older]))
    message = NS(
        id=100,
        content="히나야 아까 거 뭐야?",
        author=author,
        channel=channel,
        attachments=[],
        stickers=[],
    )
    downloader = AsyncMock(return_value=PNG)

    visuals = await collect_visual_inputs(
        message,
        include_recent=True,
        recent_message_limit=1,
        downloader=downloader,
    )

    assert [(v.name, v.context_kind, v.reference_strength) for v in visuals] == [
        ("newest.png", "recent_channel_message", "passive_recent")
    ]
    older_attachment.read.assert_not_awaited()
    downloader.assert_not_awaited()
    assert "약한 최근 문맥" in visuals[0].label(1)


@pytest.mark.asyncio
async def test_reply_and_recent_visuals_keep_distinct_message_provenance():
    reply_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="reply.png",
        read=AsyncMock(return_value=PNG),
    )
    older_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="older.png",
        read=AsyncMock(return_value=PNG),
    )
    channel = NS(id=10)
    reply_target = NS(
        id=110,
        content="",
        author=NS(id=100, display_name="사용자", name="사용자", bot=False),
        channel=channel,
        webhook_id=None,
        attachments=[reply_attachment],
        stickers=[],
    )
    older = NS(
        id=109,
        content="",
        author=NS(id=100, display_name="사용자", name="사용자", bot=False),
        channel=channel,
        webhook_id=None,
        attachments=[older_attachment],
        stickers=[],
    )
    channel.history = lambda **kwargs: _history([reply_target, older])
    message = NS(
        id=111,
        content="히나야 이 사진",
        author=NS(id=100, display_name="사용자", name="사용자", bot=False),
        channel=channel,
        reference=NS(message_id=110, channel_id=10, resolved=reply_target),
        attachments=[],
        stickers=[],
    )

    visuals = await collect_visual_inputs(
        message,
        include_reply=True,
        include_recent=True,
    )

    assert [(v.name, v.context_kind) for v in visuals] == [
        ("reply.png", "replied_message"),
        ("older.png", "recent_channel_message"),
    ]
    assert reply_attachment.read.await_count == 1
    assert older_attachment.read.await_count == 1


@pytest.mark.asyncio
async def test_fetches_reply_origin_visual_directly_by_message_id():
    attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="embarrassing.png",
        read=AsyncMock(return_value=PNG),
    )
    author = NS(id=100, display_name="사용자", name="사용자", bot=False)
    origin = NS(
        id=130,
        content="",
        author=author,
        attachments=[attachment],
        stickers=[],
    )
    channel = NS(id=10, fetch_message=AsyncMock(return_value=origin))
    message = NS(
        id=133,
        content="히나야 고마워",
        author=author,
        channel=channel,
        attachments=[],
        stickers=[],
    )

    visuals = await collect_visual_inputs(message, context_message_ids=("130",))

    channel.fetch_message.assert_awaited_once_with(130)
    assert [(v.name, v.context_kind, v.reference_strength) for v in visuals] == [
        ("embarrassing.png", "reply_origin_source", "prior_explicit_reply")
    ]
    assert "답장 체인의 강한 참조" in visuals[0].label(1)


@pytest.mark.asyncio
async def test_missing_reply_origin_visual_is_not_substituted():
    channel = NS(id=10, fetch_message=AsyncMock(side_effect=discord.NotFound(
        NS(status=404, reason="not found"), "missing"
    )))
    message = NS(
        id=133,
        content="히나야 고마워",
        author=NS(id=100, display_name="사용자", name="사용자", bot=False),
        channel=channel,
        attachments=[],
        stickers=[],
    )

    assert await collect_visual_inputs(message, context_message_ids=("130",)) == []


@pytest.mark.asyncio
async def test_embed_reply_keeps_older_visual_as_separately_labeled_context():
    recent_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="mushroom.png",
        read=AsyncMock(return_value=PNG),
    )
    channel = NS(id=10)
    reply_target = NS(
        id=120,
        content="https://example.com/post",
        author=NS(id=200, display_name="링크 봇", name="링크 봇", bot=True),
        channel=channel,
        webhook_id=None,
        attachments=[],
        stickers=[],
        embeds=[NS(image=NS(url="https://example.com/embed.png"))],
    )
    recent = NS(
        id=119,
        content="히나야 버섯 씌워놨어",
        author=NS(id=100, display_name="사용자", name="사용자", bot=False),
        webhook_id=None,
        attachments=[recent_attachment],
        stickers=[],
    )
    channel.history = lambda **kwargs: _history([reply_target, recent])
    message = NS(
        id=121,
        content="히나야 히나 고양이야?",
        author=NS(id=100, display_name="사용자", name="사용자", bot=False),
        channel=channel,
        reference=NS(message_id=120, channel_id=10, resolved=reply_target),
        attachments=[],
        stickers=[],
    )

    visuals = await collect_visual_inputs(
        message,
        include_reply=True,
        include_recent=True,
    )

    assert [(v.name, v.context_kind, v.message_content) for v in visuals] == [
        (
            "mushroom.png",
            "recent_channel_message",
            "히나야 버섯 씌워놨어",
        ),
    ]


@pytest.mark.asyncio
async def test_image_only_trigger_reaches_llm_with_ephemeral_visual_context():
    observed = []

    async def answer(*args, **kwargs):
        observed.extend(CURRENT_VISUAL_INPUTS.get())
        return "사진은 잘 보여."

    llm = NS(
        answer=AsyncMock(side_effect=answer),
        summarize=AsyncMock(),
        extract_structured_memory=AsyncMock(),
        summarize_shared=AsyncMock(),
        close=AsyncMock(),
    )
    store = Store(":memory:")
    bot = HinaClient(Settings("test", "test", cooldown=0), store=store, llm=llm)
    bot._connection.user = NS(id=99)
    channel = MagicMock()
    channel.id = 10
    channel.send = AsyncMock(return_value=NS(id=1000))
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
    channel.history.side_effect = lambda **kwargs: _history([])
    author = NS(
        id=100,
        bot=False,
        display_name="사용자",
        guild_permissions=NS(manage_guild=False),
    )
    attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="photo.png",
        read=AsyncMock(return_value=PNG),
    )
    message = NS(
        id=1,
        content="히나야",
        author=author,
        guild=None,
        channel=channel,
        mentions=[],
        webhook_id=None,
        reference=None,
        attachments=[attachment],
        stickers=[],
    )

    try:
        await bot.on_message(message)
        llm.answer.assert_awaited_once()
        assert len(observed) == 1
        assert observed[0].source == "attachment"
        assert observed[0].name == "photo.png"
        assert observed[0].reference_strength == "current_message"
        assert message.content == "히나야"
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_passive_recent_visual_alone_does_not_turn_bare_call_into_image_request():
    llm = NS(
        answer=AsyncMock(return_value="이미지 답변"),
        summarize=AsyncMock(),
        extract_structured_memory=AsyncMock(),
        summarize_shared=AsyncMock(),
        close=AsyncMock(),
    )
    store = Store(":memory:")
    bot = HinaClient(Settings("test", "test", cooldown=0), store=store, llm=llm)
    bot._connection.user = NS(id=99)
    attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="old.png",
        read=AsyncMock(return_value=PNG),
    )
    author = NS(
        id=100,
        bot=False,
        display_name="사용자",
        guild_permissions=NS(manage_guild=False),
    )
    old = NS(
        id=1,
        content="히나야 이 사진 봐",
        author=author,
        webhook_id=None,
        attachments=[attachment],
        stickers=[],
        mentions=[],
        guild=None,
    )
    channel = MagicMock()
    channel.id = 10
    channel.send = AsyncMock(return_value=NS(id=1000))
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
    channel.history.side_effect = lambda **kwargs: _history([old])
    old.channel = channel
    message = NS(
        id=2,
        content="히나야",
        author=author,
        guild=None,
        channel=channel,
        mentions=[],
        webhook_id=None,
        reference=None,
        attachments=[],
        stickers=[],
    )

    try:
        await bot.on_message(message)
        llm.answer.assert_not_awaited()
        assert channel.send.await_args.args[0] == bot.settings.empty_call_reply
        assert message.content == "히나야"
    finally:
        await bot.close()
