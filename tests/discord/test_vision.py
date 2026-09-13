from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.discord.vision import collect_visual_inputs

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
