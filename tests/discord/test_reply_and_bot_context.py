from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from hina_bot.config import Settings
from hina_bot.routing import Scope
from hina_bot.store import Store
from hina_bot.web_bot import HinaClient

from hina_bot.discord.chatlog_capture import set_capture_mode_override
from hina_bot.discord.reply_context import REPLY_CONTEXT, collect_reply_context
from hina_bot.discord.target_recent import TargetAwareRecentMessages


class FakeHistoryChannel:
    def __init__(self, channel_id, messages=()):
        self.id = channel_id
        self.messages = list(messages)

    def history(self, **kwargs):
        async def rows():
            for message in self.messages:
                yield message

        return rows()


@pytest.mark.asyncio
async def test_explicit_reply_is_available_even_when_direct_capture_omits_side_chat():
    store = Store(":memory:")
    try:
        set_capture_mode_override(store, "global", "direct")
        recent = TargetAwareRecentMessages(store=store)
        scope = Scope(1, 10, 100)
        now = datetime.now(UTC)
        channel = FakeHistoryChannel(10)
        target = NS(
            id=41,
            content="왈랄와와와라오라아",
            author=NS(id=200, bot=False, display_name="대상", name="대상"),
            webhook_id=None,
            created_at=now - timedelta(seconds=10),
            channel=channel,
        )
        message = NS(
            id=42,
            content="히나야 이 사람 왜 이럴까",
            author=NS(id=100, bot=False),
            channel=channel,
            reference=NS(message_id=41, channel_id=10, resolved=target),
        )

        rows = await collect_reply_context(message, 99)
        token = REPLY_CONTEXT.set(tuple(rows))
        try:
            context = recent.context(scope, message.id)
        finally:
            REPLY_CONTEXT.reset(token)

        assert len(context) == 1
        assert context[0]["content"] == "왈랄와와와라오라아"
        assert context[0]["context_kind"] == "replied_message"
        assert context[0]["reference_strength"] == "explicit_reply"
        assert context[0]["user_id"] == "200"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_reply_to_other_bot_is_marked_as_bot_context():
    now = datetime.now(UTC)
    channel = FakeHistoryChannel(10)
    target = NS(
        id=51,
        content="히대콤",
        author=NS(id=300, bot=True, display_name="다른 봇", name="다른 봇"),
        webhook_id=None,
        created_at=now,
        channel=channel,
    )
    message = NS(
        id=52,
        content="히나야 얘 왜 이래",
        author=NS(id=100, bot=False),
        channel=channel,
        reference=NS(message_id=51, channel_id=10, resolved=target),
    )

    rows = await collect_reply_context(message, 99)

    assert rows[0]["role"] == "bot"
    assert rows[0]["content"] == "히대콤"


@pytest.fixture
async def client():
    store = Store(":memory:")
    llm = NS(close=AsyncMock())
    bot = HinaClient(Settings("test", "test", cooldown=0), store=store, llm=llm)
    bot._connection.user = NS(id=99)
    try:
        yield bot, store
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_other_bot_live_message_is_captured_in_all_mode(client):
    bot, _ = client
    channel = FakeHistoryChannel(10)
    message = NS(
        id=60,
        content="히대콤",
        author=NS(id=300, bot=True, display_name="다른 봇"),
        webhook_id=None,
        guild=NS(id=1),
        channel=channel,
        mentions=[],
    )

    await bot.on_message(message)

    rows = bot.recent.context(Scope(1, 10, 100), 99)
    assert [(row["content"], row["role"]) for row in rows] == [("히대콤", "bot")]


@pytest.mark.asyncio
async def test_other_bot_live_message_is_omitted_in_direct_mode(client):
    bot, store = client
    set_capture_mode_override(store, "global", "direct")
    channel = FakeHistoryChannel(10)
    message = NS(
        id=61,
        content="히대콤",
        author=NS(id=300, bot=True, display_name="다른 봇"),
        webhook_id=None,
        guild=NS(id=1),
        channel=channel,
        mentions=[],
    )

    await bot.on_message(message)

    assert bot.recent.context(Scope(1, 10, 100), 99) == []


@pytest.mark.asyncio
async def test_history_hydration_keeps_other_bots_only_in_all_mode(client):
    bot, store = client
    now = datetime.now(UTC)
    bot_message = NS(
        id=70,
        content="다른 봇의 최근 말",
        author=NS(id=300, bot=True, display_name="다른 봇"),
        webhook_id=None,
        created_at=now - timedelta(minutes=1),
        mentions=[],
        guild=NS(id=1),
    )
    channel = FakeHistoryChannel(10, [bot_message])
    current = NS(id=80, created_at=now, channel=channel)
    scope = Scope(1, 10, 100)

    await bot.hydrate_recent_history(current, scope)
    rows = bot.recent.context(scope, current.id)
    assert [(row["content"], row["role"]) for row in rows] == [
        ("다른 봇의 최근 말", "bot")
    ]

    bot.recent.clear_channel(scope)
    set_capture_mode_override(store, "global", "direct")
    await bot.hydrate_recent_history(current, scope)
    assert bot.recent.context(scope, current.id) == []
