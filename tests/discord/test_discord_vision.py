from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest
from hina_bot.config import Settings
from hina_bot.store import Store
from hina_bot.web_bot import HinaClient

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS
from hina_bot.discord.vision import VisionLimits, collect_visual_inputs

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16
GIF = b"GIF89a" + b"x" * 16


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
async def test_image_only_trigger_reaches_llm_with_ephemeral_visual_context():
    observed = []

    async def answer(*args, **kwargs):
        observed.extend(CURRENT_VISUAL_INPUTS.get())
        return "사진은 잘 보여."

    llm = NS(
        answer=AsyncMock(side_effect=answer),
        summarize=AsyncMock(),
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
        attachments=[attachment],
        stickers=[],
    )

    try:
        await bot.on_message(message)
        llm.answer.assert_awaited_once()
        assert len(observed) == 1
        assert observed[0].source == "attachment"
        assert observed[0].name == "photo.png"
        assert message.content == "히나야"
    finally:
        await bot.close()
