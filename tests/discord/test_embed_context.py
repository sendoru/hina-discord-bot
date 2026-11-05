"""Discord link embed metadata is only a bounded, attributed context source."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from hina_bot.core.recent import RecentMessages
from hina_bot.core.routing import Scope
from hina_bot.discord.embed_context import MAX_EMBED_TEXT_CHARS, message_embed_text
from hina_bot.discord.forward_context import message_context_text
from hina_bot.discord.reply_context import collect_reply_context
from hina_bot.discord.web_bot import HinaClient


def preview(url="https://x.com/example/status/1", *, title="공지", description="즐거운 할로윈!",
            author="원본 게시자", image=None, thumbnail=None, fields=()):
    return NS(
        url=url, title=title, description=description,
        author=NS(name=author),
        fields=[NS(name=name, value=value) for name, value in fields],
        image=image, thumbnail=thumbnail,
    )


def message(mid, text="", embeds=()):
    return NS(
        id=mid, content=text, embeds=list(embeds), attachments=[], stickers=[],
        author=NS(id=100, bot=False, display_name="링크 공유자"),
        channel=NS(id=10), guild=NS(id=1),
        webhook_id=None, reference=None, mentions=[],
        created_at=datetime.now(UTC),
    )


def test_embed_metadata_is_attributed_to_original_author_not_discord_sender():
    original_url = "https://x.com/example/status/1"
    item = message(1, "히나야 이거 봐 " + original_url, [
        preview(url=original_url, fields=[("원문", "다들 해피 할로윈!!!")]),
    ])
    text = message_context_text(item)
    assert text.startswith(item.content)
    assert "Discord 링크 미리보기" in text
    assert "원본 작성자/표시 이름: 원본 게시자" in text
    assert "다들 해피 할로윈!!!" in text
    assert text.count(original_url) == 1
    assert item.content == "히나야 이거 봐 " + original_url


def test_embed_projection_deduplicates_and_limits_fields_and_total_size():
    item = message(2, "링크", [
        preview(description="z" * 9000, fields=[(str(i), "y" * 1000) for i in range(20)]),
        preview(description="z" * 9000),
        preview(url="https://example.org/3", title="ignored"),
    ])
    preview_text = message_embed_text(item)
    assert len(preview_text) <= MAX_EMBED_TEXT_CHARS
    assert preview_text.count("Discord 링크 미리보기") == 1
    assert "ignored" not in preview_text
    assert "4: " not in preview_text


def test_embed_absent_keeps_original_text_and_ignores_empty_previews():
    assert message_context_text(message(3, "평범한 채팅")) == "평범한 채팅"
    assert message_embed_text(message(4, "링크", [preview(
        url="", title="", description="", author="", fields=[],
    )])) == ""


@pytest.mark.asyncio
async def test_explicit_reply_receives_embedded_reference_with_source_identity():
    target = message(10, "공유한 링크", [preview(description="게시물의 본문")])
    request = message(11, "히나야 이건?", [])
    request.reference = NS(channel_id=10, message_id=10, resolved=target)
    rows = await collect_reply_context(request, bot_id=99)
    assert len(rows) == 1
    assert rows[0]["author_user_id"] == "100"
    assert rows[0]["context_kind"] == "replied_message"
    assert "게시물의 본문" in rows[0]["content"]


@pytest.mark.asyncio
async def test_embed_resolution_updates_existing_recent_row_without_creating_a_turn():
    recent = RecentMessages()
    scope = Scope(1, 10, 100)
    recent.add(scope, 15, "링크 공유자", "https://x.com/example/status/1",
               author_user_id=100, direct_trigger=False)
    fake = NS(recent=recent, visual_cache=NS(invalidate=Mock()))
    original = message(15, "https://x.com/example/status/1")
    updated = message(15, original.content, [preview(description="나중에 생성된 미리보기")])
    await HinaClient.on_message_edit(fake, original, updated)
    rows = list(recent.buffers[recent._key(scope)])
    assert len(rows) == 1
    assert rows[0]["direct_trigger"] is False
    assert "나중에 생성된 미리보기" in rows[0]["content"]
    fake.visual_cache.invalidate.assert_called_once_with(10, 15)

    # No new admission if the message was not in the captured channel context.
    unknown = message(16, "새 링크", [preview(description="새로운 메시지")])
    await HinaClient.on_message_edit(fake, original, unknown)
    assert len(recent.buffers[recent._key(scope)]) == 1


@pytest.mark.asyncio
async def test_preflight_only_rereads_unresolved_links_once_via_discord():
    original = message(17, "히나야 https://x.com/example/status/1")
    updated = message(17, original.content, [preview()])
    original.channel.fetch_message = AsyncMock(return_value=updated)
    assert await HinaClient._refresh_missing_embed(original) is updated
    original.channel.fetch_message.assert_awaited_once_with(17)
    updated.channel.fetch_message = AsyncMock()
    assert await HinaClient._refresh_missing_embed(updated) is updated
    updated.channel.fetch_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_embed_refresh_rejects_mismatched_message_identity():
    original = message(18, "히나야 https://example.org")
    unrelated = message(19, original.content, [preview()])
    original.channel.fetch_message = AsyncMock(return_value=unrelated)
    assert await HinaClient._refresh_missing_embed(original) is original
