from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.vision import (
    VisualContextRef,
    VisionLimits,
    collect_visual_inputs,
    message_has_visual,
    rank_visual_context_refs,
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


def test_visual_context_ranking_prefers_causal_context_before_recency():
    rows = [
        {
            "message_id": "300",
            "has_visual": True,
            "context_kind": "channel_ambient",
        },
        {
            "message_id": "100",
            "has_visual": True,
            "context_kind": "replied_message",
            "reference_strength": "explicit_reply",
        },
        {
            "message_id": "250",
            "has_visual": True,
            "context_kind": "speaker_thread",
            "reference_strength": "same_speaker",
        },
        {
            "message_id": "240",
            "has_visual": True,
            "context_kind": "speaker_thread",
            "reference_strength": "same_speaker",
        },
    ]

    refs = rank_visual_context_refs(rows)

    assert [(ref.message_id, ref.context_kind) for ref in refs] == [
        ("100", "replied_message"),
        ("250", "speaker_thread"),
        ("240", "speaker_thread"),
        ("300", "channel_ambient"),
    ]


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
async def test_source_quotas_are_shared_across_selected_message_contexts():
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
    target = NS(
        id=40,
        content="",
        author=NS(id=200, display_name="대상", name="대상"),
        attachments=[reply_attachment],
        stickers=[],
    )
    channel = NS(id=10, fetch_message=AsyncMock(return_value=target))
    message = NS(
        id=41,
        content="히나야 이거 봐",
        author=NS(id=100, display_name="사용자", name="사용자"),
        channel=channel,
        attachments=[current_attachment],
        stickers=[],
    )
    refs = [VisualContextRef(
        message_id="40",
        context_kind="replied_message",
        reference_strength="explicit_reply",
    )]

    visuals = await collect_visual_inputs(
        message,
        context_refs=refs,
        limits=VisionLimits(attachments=1, emojis=0, stickers=0),
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
async def test_collects_selected_explicit_reply_visual_with_provenance():
    attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="older.png",
        read=AsyncMock(return_value=PNG),
    )
    target = NS(
        id=70,
        content="이거",
        author=NS(id=200, display_name="민수", name="민수"),
        attachments=[attachment],
        stickers=[],
    )
    channel = NS(id=10, fetch_message=AsyncMock(return_value=target))
    message = NS(
        id=71,
        content="히나야 이건 뭐야?",
        author=NS(id=100, display_name="사용자", name="사용자"),
        channel=channel,
        attachments=[],
        stickers=[],
    )
    refs = [VisualContextRef(
        message_id="70",
        context_kind="replied_message",
        reference_strength="explicit_reply",
    )]

    visuals = await collect_visual_inputs(message, context_refs=refs)

    assert len(visuals) == 1
    visual = visuals[0]
    assert visual.context_kind == "replied_message"
    assert visual.reference_strength == "explicit_reply"
    assert visual.message_id == "70"
    assert visual.author_name == "민수"
    assert "명시적 답장 대상 메시지" in visual.label(1)
    assert "강한 참조" in visual.label(1)


@pytest.mark.asyncio
async def test_unselected_visual_is_never_scanned_or_substituted():
    attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="unrelated.png",
        read=AsyncMock(return_value=PNG),
    )
    unrelated = NS(
        id=80,
        content="",
        author=NS(id=200, display_name="다른 사용자", name="다른 사용자"),
        attachments=[attachment],
        stickers=[],
    )
    channel = NS(
        id=10,
        fetch_message=AsyncMock(),
        history=MagicMock(return_value=_history([unrelated])),
    )
    message = NS(
        id=81,
        content="히나야",
        author=NS(id=100, display_name="사용자", name="사용자"),
        channel=channel,
        attachments=[],
        stickers=[],
    )

    visuals = await collect_visual_inputs(message, context_refs=[])

    assert visuals == []
    channel.fetch_message.assert_not_awaited()
    channel.history.assert_not_called()
    attachment.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_ranked_context_fetches_until_visual_message_cap_and_ignores_passive_emojis():
    reply_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="reply.png",
        read=AsyncMock(return_value=PNG),
    )
    speaker_attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="speaker.png",
        read=AsyncMock(return_value=PNG),
    )
    author = NS(id=100, display_name="사용자", name="사용자", bot=False)
    reply = NS(
        id=90, content="", author=author,
        attachments=[reply_attachment], stickers=[],
    )
    emoji_only = NS(
        id=95, content="<:old_emoji:123>", author=author,
        attachments=[], stickers=[],
    )
    speaker = NS(
        id=94, content="문제 사진", author=author,
        attachments=[speaker_attachment], stickers=[],
    )
    by_id = {90: reply, 95: emoji_only, 94: speaker}
    channel = NS(id=10, fetch_message=AsyncMock(side_effect=lambda mid: by_id[mid]))
    message = NS(
        id=100, content="히나야 다시 봐줘", author=author, channel=channel,
        attachments=[], stickers=[],
    )
    rows = [
        {
            "message_id": "95", "has_visual": True,
            "context_kind": "speaker_thread", "reference_strength": "same_speaker",
        },
        {
            "message_id": "94", "has_visual": True,
            "context_kind": "speaker_thread", "reference_strength": "same_speaker",
        },
        {
            "message_id": "90", "has_visual": True,
            "context_kind": "replied_message", "reference_strength": "explicit_reply",
        },
    ]
    refs = rank_visual_context_refs(rows)

    visuals = await collect_visual_inputs(
        message,
        context_refs=refs,
        max_context_messages=2,
        downloader=AsyncMock(return_value=PNG),
    )

    assert [(v.name, v.context_kind) for v in visuals] == [
        ("reply.png", "replied_message"),
        ("speaker.png", "speaker_thread"),
    ]
    assert [call.args[0] for call in channel.fetch_message.await_args_list] == [90, 95, 94]


@pytest.mark.asyncio
async def test_selected_visuals_keep_distinct_message_provenance():
    reply_attachment = NS(
        size=len(PNG), content_type="image/png", filename="reply.png",
        read=AsyncMock(return_value=PNG),
    )
    speaker_attachment = NS(
        size=len(PNG), content_type="image/png", filename="speaker.png",
        read=AsyncMock(return_value=PNG),
    )
    author = NS(id=100, display_name="사용자", name="사용자", bot=False)
    messages = {
        110: NS(id=110, content="", author=author,
                attachments=[reply_attachment], stickers=[]),
        109: NS(id=109, content="", author=author,
                attachments=[speaker_attachment], stickers=[]),
    }
    channel = NS(id=10, fetch_message=AsyncMock(side_effect=lambda mid: messages[mid]))
    message = NS(
        id=111, content="히나야 이 사진", author=author, channel=channel,
        attachments=[], stickers=[],
    )
    refs = [
        VisualContextRef("110", "replied_message", "explicit_reply"),
        VisualContextRef("109", "speaker_thread", "same_speaker"),
    ]

    visuals = await collect_visual_inputs(message, context_refs=refs)

    assert [(v.name, v.context_kind) for v in visuals] == [
        ("reply.png", "replied_message"),
        ("speaker.png", "speaker_thread"),
    ]


@pytest.mark.asyncio
async def test_fetches_selected_reply_origin_visual_directly_by_message_id():
    attachment = NS(
        size=len(PNG), content_type="image/png", filename="embarrassing.png",
        read=AsyncMock(return_value=PNG),
    )
    author = NS(id=100, display_name="사용자", name="사용자", bot=False)
    origin = NS(id=130, content="", author=author, attachments=[attachment], stickers=[])
    channel = NS(id=10, fetch_message=AsyncMock(return_value=origin))
    message = NS(
        id=133, content="히나야 고마워", author=author, channel=channel,
        attachments=[], stickers=[],
    )
    refs = [VisualContextRef(
        "130", "reply_origin_source", "prior_explicit_reply",
    )]

    visuals = await collect_visual_inputs(message, context_refs=refs)

    channel.fetch_message.assert_awaited_once_with(130)
    assert [(v.name, v.context_kind, v.reference_strength) for v in visuals] == [
        ("embarrassing.png", "reply_origin_source", "prior_explicit_reply")
    ]
    assert "답장 체인의 강한 참조" in visuals[0].label(1)


@pytest.mark.asyncio
async def test_missing_selected_visual_is_not_substituted_from_history():
    history = MagicMock(return_value=_history([]))
    channel = NS(
        id=10,
        fetch_message=AsyncMock(side_effect=discord.NotFound(
            NS(status=404, reason="not found"), "missing"
        )),
        history=history,
    )
    message = NS(
        id=133, content="히나야 고마워",
        author=NS(id=100, display_name="사용자", name="사용자", bot=False),
        channel=channel, attachments=[], stickers=[],
    )
    refs = [VisualContextRef("130", "speaker_thread", "same_speaker")]

    assert await collect_visual_inputs(message, context_refs=refs) == []
    history.assert_not_called()


@pytest.mark.asyncio
async def test_selected_speaker_visual_does_not_depend_on_nearby_embed_reply():
    recent_attachment = NS(
        size=len(PNG), content_type="image/png", filename="mushroom.png",
        read=AsyncMock(return_value=PNG),
    )
    author = NS(id=100, display_name="사용자", name="사용자", bot=False)
    recent = NS(
        id=119, content="히나야 버섯 씌워놨어", author=author,
        attachments=[recent_attachment], stickers=[],
    )
    channel = NS(id=10, fetch_message=AsyncMock(return_value=recent))
    message = NS(
        id=121, content="히나야 히나 고양이야?", author=author, channel=channel,
        attachments=[], stickers=[],
    )
    refs = [VisualContextRef("119", "speaker_thread", "same_speaker")]

    visuals = await collect_visual_inputs(message, context_refs=refs)

    assert [(v.name, v.context_kind, v.message_content) for v in visuals] == [
        ("mushroom.png", "speaker_thread", "히나야 버섯 씌워놨어")
    ]

@pytest.mark.asyncio
async def test_selected_speaker_context_drives_text_and_visual_from_one_snapshot():
    observed_context = []
    observed_visuals = []

    async def answer(*args, **kwargs):
        observed_context.extend(kwargs["channel_context"])
        observed_visuals.extend(CURRENT_VISUAL_INPUTS.get())
        return "다시 풀어볼게."

    llm = NS(
        answer=AsyncMock(side_effect=answer),
        summarize=AsyncMock(),
        extract_structured_memory=AsyncMock(),
        summarize_shared=AsyncMock(),
        close=AsyncMock(),
    )
    store = Store(":memory:")
    store.set_memory_mode_override("global", "off")
    store.set_chat_log_mode_override("global", "on")
    bot = HinaClient(Settings("test", "test", cooldown=0), store=store, llm=llm)
    bot._connection.user = NS(id=99, display_name="히나")
    bot.emoji_registry.catalog = AsyncMock(return_value=[])

    attachment = NS(
        size=len(PNG),
        content_type="image/png",
        filename="problem.png",
        read=AsyncMock(return_value=PNG),
    )
    author = NS(
        id=100,
        bot=False,
        display_name="사용자",
        guild_permissions=NS(manage_guild=False),
    )
    visual_source = NS(
        id=10,
        content="자 여깄어",
        author=author,
        attachments=[attachment],
        stickers=[],
    )
    channel = MagicMock()
    channel.id = 20
    channel.fetch_message = AsyncMock(return_value=visual_source)
    channel.history.side_effect = lambda **kwargs: _history([])
    channel.send = AsyncMock(return_value=NS(id=1000, created_at=None))
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
    guild = NS(id=1, default_role=NS())
    scope = Scope(1, 20, 100)
    other = Scope(1, 20, 200)

    bot.recent.add(
        scope,
        10,
        "사용자",
        "자 여깄어",
        direct_trigger=True,
        has_visual=True,
    )
    bot.recent.add(
        scope,
        11,
        "히나",
        "이전 답변",
        role="assistant",
        author_user_id=99,
        reply_target_user_id=100,
    )
    for message_id in range(12, 28):
        bot.recent.add(other, message_id, "다른 사용자", f"주변 텍스트 {message_id}")
    bot.recent.mark_hydrated(scope)

    real_context = bot.recent.context
    bot.recent.context = MagicMock(side_effect=real_context)

    message = NS(
        id=30,
        content="히나야 괜찮으면 다시 풀어봐",
        author=author,
        guild=guild,
        channel=channel,
        mentions=[],
        webhook_id=None,
        reference=None,
        attachments=[],
        stickers=[],
        created_at=None,
    )

    try:
        await bot.on_message(message)

        llm.answer.assert_awaited_once()
        assert bot.recent.context.call_count == 1
        selected = next(row for row in observed_context if str(row["message_id"]) == "10")
        assert selected["context_kind"] == "speaker_thread"
        assert selected["has_visual"] is True
        assert [(v.message_id, v.context_kind) for v in observed_visuals] == [
            ("10", "speaker_thread")
        ]
        channel.fetch_message.assert_awaited_once_with(10)
        channel.history.assert_not_called()
    finally:
        await bot.close()


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
