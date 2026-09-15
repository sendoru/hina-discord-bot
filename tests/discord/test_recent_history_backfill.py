import time
import unittest
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

AVAILABLE = find_spec("discord") is not None

from hina_bot.core.recent import RecentMessages
from hina_bot.core.routing import Scope

if AVAILABLE:
    from hina_bot.core.config import Settings
    from hina_bot.core.store import Store
    from hina_bot.discord.bot import HinaClient


class RecentBufferTests(unittest.TestCase):
    def test_historical_rows_keep_order_and_expire_by_original_time(self):
        recent = RecentMessages(ttl=900)
        scope = Scope(1, 10, 100)
        now = time.time()
        recent.add(scope, 30, "current", "current")
        recent.add(scope, 10, "old", "old", unix_time=now - 60)
        recent.add(scope, 5, "expired", "expired", unix_time=now - 901)

        rows = recent.context(scope, 100)
        self.assertEqual([row["message_id"] for row in rows], [10, 30])

    def test_clear_requires_hydration_again(self):
        recent = RecentMessages()
        scope = Scope(1, 10, 100)
        self.assertTrue(recent.needs_hydration(scope))
        recent.mark_hydrated(scope)
        self.assertFalse(recent.needs_hydration(scope))
        recent.clear_channel(scope)
        self.assertTrue(recent.needs_hydration(scope))


class FakeHistoryChannel:
    def __init__(self, messages):
        self.messages = messages
        self.kwargs = None

    def history(self, **kwargs):
        self.kwargs = kwargs

        async def rows():
            for message in self.messages:
                yield message

        return rows()


@unittest.skipUnless(AVAILABLE, "Install project dependencies to test Discord history backfill")
class HistoryBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.llm = NS(close=AsyncMock())
        self.bot = HinaClient(Settings("test", "test", cooldown=0), store=self.store, llm=self.llm)
        self.bot._connection.user = NS(id=99)

    async def asyncTearDown(self):
        await self.bot.close()

    @staticmethod
    def old_message(message_id, text, created_at, author_id=100, *, bot=False):
        return NS(
            id=message_id,
            content=text,
            created_at=created_at,
            author=NS(id=author_id, bot=bot, display_name=f"user-{author_id}"),
            webhook_id=None,
            mentions=[],
            guild=NS(id=1),
        )

    async def test_backfill_recovers_recent_off_period_without_old_messages(self):
        now = datetime.now(UTC)
        messages = [
            self.old_message(1, "너무 오래된 대화", now - timedelta(minutes=20)),
            self.old_message(2, "최근 일반 대화", now - timedelta(minutes=5)),
            self.old_message(3, "히나야 /메모 숨길 내용", now - timedelta(minutes=4)),
            self.old_message(4, "다른 봇", now - timedelta(minutes=3), author_id=77, bot=True),
            self.old_message(5, "이전 히나 답변", now - timedelta(minutes=2), author_id=99, bot=True),
        ]
        channel = FakeHistoryChannel(messages)
        current = NS(id=10, created_at=now, channel=channel)
        scope = Scope(1, 10, 200, True)

        # This is what off -> on looks like locally: the old in-memory buffer was cleared.
        self.bot.recent.clear_channel(scope)
        self.assertTrue(self.bot.recent.needs_hydration(scope))
        await self.bot.hydrate_recent_history(current, scope)

        rows = self.bot.recent.context(scope, current.id)
        self.assertEqual([row["content"] for row in rows], ["최근 일반 대화", "히나야 /메모 숨길 내용", "이전 히나 답변"])
        self.assertEqual([row["role"] for row in rows], ["user", "user", "assistant"])
        self.assertFalse(self.bot.recent.needs_hydration(scope))
        self.assertEqual(channel.kwargs["before"], current)
        self.assertEqual(channel.kwargs["limit"], self.bot.recent.limit)
        self.assertGreater(channel.kwargs["after"], now - timedelta(minutes=16))
