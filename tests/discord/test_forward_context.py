"""Regression coverage: a Discord Forward is one message before a separate follow-up.

The original author's identity is not included in Discord's MessageSnapshot. We only
use the forwarded text as attributed reference material, never as a live DM exchange.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.egress_policy import filter_channel_context
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.chatlog_capture import set_capture_mode_override
from hina_bot.discord.forward_context import message_context_text
from hina_bot.discord.reply_context import collect_reply_context
from hina_bot.discord.web_bot import HinaClient


FORWARD_TEXT = "(네 옷자락을 손가락으로 꼭 쥐며) ...계속 있어 줄게."


def forwarded_message(message_id=71, *, content="", timestamp=None):
    return NS(
        id=message_id,
        content=content,
        message_snapshots=[NS(content=FORWARD_TEXT)],
        created_at=timestamp or datetime.now(UTC),
        author=NS(id=100, bot=False, display_name="danger", name="danger"),
        webhook_id=None,
        guild=NS(id=1, name="test-guild"),
        channel=NS(id=10, name="bug-report"),
        mentions=[],
        attachments=[],
        stickers=[],
        reference=None,
    )


def test_forward_projection_preserves_source_boundary_and_ordinary_content():
    message = forwarded_message()
    context = message_context_text(message)
    assert FORWARD_TEXT in context
    assert context.startswith("[Discord 전달 메시지")
    assert "원 작성자 확인 불가" in context
    assert message.content == ""  # The source message itself remains untouched.

    mixed = forwarded_message(content="여기 봐줘")
    assert message_context_text(mixed).startswith("여기 봐줘\n\n[Discord 전달 메시지")
    ordinary = NS(content="일반 메시지")
    assert message_context_text(ordinary) == "일반 메시지"
    empty = NS(content="", message_snapshots=[NS(content="")])
    assert message_context_text(empty) == ""


@pytest.fixture
async def client():
    store = Store.create(":memory:")
    bot = HinaClient(
        Settings(
            discord_token="test",
            openai_api_key="test",
            external_context_policy="full",
            cooldown=0,
        ),
        store=store,
        llm=NS(close=AsyncMock()),
    )
    bot._connection.user = NS(id=99, display_name="히나")
    try:
        yield bot, store
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_forward_only_previous_message_is_available_to_followup_in_full_mode(client):
    bot, store = client
    forwarded = forwarded_message(message_id=71)
    followup = NS(id=72, content="히나 오류 낫어요")

    # First message is forwarded without a call prefix; the next message calls Hina.
    await bot.on_message(forwarded)
    scope = Scope(1, 10, 100)
    rows = bot.recent.context(scope, followup.id)

    assert store.chat_log_enabled(scope)
    assert len(rows) == 1
    assert rows[0]["message_id"] == 71
    assert rows[0]["context_kind"] == "speaker_thread"
    assert rows[0]["direct_trigger"] is False
    assert FORWARD_TEXT in rows[0]["content"]
    assert len(filter_channel_context(rows, scope.user_id, "full")) == 1
    assert filter_channel_context(rows, scope.user_id, "bot_interactions_only") == []


@pytest.mark.asyncio
async def test_forward_only_message_is_excluded_by_direct_capture(client):
    bot, store = client
    set_capture_mode_override(store, "global", "direct")
    await bot.on_message(forwarded_message())
    assert bot.recent.context(Scope(1, 10, 100), 72) == []


@pytest.mark.asyncio
async def test_forward_only_message_hydrates_after_restart(client):
    bot, _ = client
    old = forwarded_message(
        timestamp=datetime.now(UTC) - timedelta(seconds=20),
    )
    class Channel:
        id = 10
        name = "bug-report"

        def history(self, **kwargs):
            async def rows():
                yield old
            return rows()

    current = NS(id=72, created_at=datetime.now(UTC), channel=Channel())
    scope = Scope(1, 10, 100)

    assert bot.recent.needs_hydration(scope)
    await bot.hydrate_recent_history(current, scope)

    rows = bot.recent.context(scope, current.id)
    assert len(rows) == 1
    assert rows[0]["context_kind"] == "speaker_thread"
    assert FORWARD_TEXT in rows[0]["content"]


@pytest.mark.asyncio
async def test_explicit_reply_to_forward_uses_snapshot_instead_of_empty_content():
    original = forwarded_message()
    reference = NS(id=10)
    original.channel = reference
    reply = NS(
        channel=reference,
        reference=NS(channel_id=10, message_id=71, resolved=original),
    )

    rows = await collect_reply_context(reply, bot_id=99)
    assert len(rows) == 1
    assert rows[0]["context_kind"] == "replied_message"
    assert rows[0]["author_user_id"] == "100"
    assert FORWARD_TEXT in rows[0]["content"]
