import time
import unittest
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

AVAILABLE = find_spec("discord") is not None

from hina_bot.ai.request_assembly import RequestAssembler
from hina_bot.core.recent import RecentMessages
from hina_bot.core.routing import Scope

if AVAILABLE:
    from hina_bot.core.config import Settings
    from hina_bot.core.store import Store
    from hina_bot.discord.bot import HinaClient
    from hina_bot.discord.target_recent import TargetAwareRecentMessages
    from hina_bot.discord.web_bot import HinaClient as WebHinaClient


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



@unittest.skipUnless(AVAILABLE, "Install project dependencies to test recent-context parity")
class RecentHydrationParityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.llm = NS(close=AsyncMock())
        self.bot = WebHinaClient(
            Settings("test", "test", cooldown=0),
            store=self.store,
            llm=self.llm,
        )
        self.bot._connection.user = NS(id=99, display_name="히나")

    async def asyncTearDown(self):
        await self.bot.close()

    @staticmethod
    def message(message_id, text, created_at, author_id, *, bot=False):
        return NS(
            id=message_id,
            content=text,
            created_at=created_at,
            author=NS(
                id=author_id,
                bot=bot,
                display_name=f"user-{author_id}",
                name=f"user-{author_id}",
            ),
            webhook_id=None,
            mentions=[],
            guild=NS(id=1),
        )

    @staticmethod
    def provider_rows(recent, scope, before_id):
        return RequestAssembler._bind_current_speaker(
            recent.context(scope, before_id),
            scope.user_id,
        )

    async def test_live_and_hydrated_recoverable_rows_match_provider_semantics(self):
        now = datetime.now(UTC)
        rows = [
            self.message(1, "주변 사람의 일반 대화", now - timedelta(minutes=3), 200),
            self.message(2, "히나야 지금 질문", now - timedelta(minutes=2), 100),
            self.message(3, "다른 봇의 주변 대화", now - timedelta(minutes=1), 300, bot=True),
        ]
        channel = FakeHistoryChannel(rows)
        current = NS(id=10, created_at=now, channel=channel)
        scope = Scope(1, 10, 100)

        live = TargetAwareRecentMessages(
            store=self.store,
            external_context_policy=self.bot.settings.external_context_policy,
        )
        live.add(
            Scope(1, 10, 200),
            1,
            "user-200",
            "주변 사람의 일반 대화",
            role="user",
            unix_time=rows[0].created_at.timestamp(),
            author_user_id=200,
            reply_target_user_id=None,
            direct_trigger=False,
        )
        live.add(
            Scope(1, 10, 100),
            2,
            "user-100",
            "히나야 지금 질문",
            role="user",
            unix_time=rows[1].created_at.timestamp(),
            author_user_id=100,
            reply_target_user_id=None,
            direct_trigger=True,
        )
        live.add(
            Scope(1, 10, 300),
            3,
            "user-300",
            "다른 봇의 주변 대화",
            role="bot",
            unix_time=rows[2].created_at.timestamp(),
            author_user_id=300,
            reply_target_user_id=None,
            direct_trigger=False,
        )

        await self.bot.hydrate_recent_history(current, scope)

        self.assertEqual(
            self.provider_rows(live, scope, current.id),
            self.provider_rows(self.bot.recent, scope, current.id),
        )

    async def test_hydrated_assistant_is_conservatively_unattributed_without_restart_metadata(self):
        now = datetime.now(UTC)
        assistant = self.message(
            5,
            "재시작 전 히나 답변",
            now - timedelta(minutes=1),
            99,
            bot=True,
        )
        channel = FakeHistoryChannel([assistant])
        current = NS(id=10, created_at=now, channel=channel)
        scope = Scope(1, 10, 100)

        await self.bot.hydrate_recent_history(current, scope)

        raw = next(iter(self.bot.recent.buffers[self.bot.recent._key(scope)]))
        self.assertEqual(raw["role"], "assistant")
        self.assertEqual(raw["author_user_id"], "99")
        self.assertIsNone(raw["reply_target_user_id"])
        self.assertIsNone(raw["direct_trigger"])

        provider = self.provider_rows(self.bot.recent, scope, current.id)
        self.assertEqual(provider[0]["context_kind"], "channel_ambient")
        self.assertNotIn("reply_target_is_current_speaker", provider[0])

    def test_live_assistant_metadata_no_longer_depends_on_timestamp_presence(self):
        recent = TargetAwareRecentMessages()
        scope = Scope(1, 10, 100)
        timestamp = datetime.now(UTC).timestamp()

        recent.add(
            scope,
            1,
            "히나",
            "live",
            role="assistant",
            unix_time=timestamp,
            author_user_id=99,
            reply_target_user_id=100,
            capture_turn_provenance=True,
        )
        recent.add(
            Scope(1, 10, 99),
            2,
            "히나",
            "hydrated",
            role="assistant",
            unix_time=None,
            author_user_id=99,
            reply_target_user_id=None,
            capture_turn_provenance=False,
        )

        rows = list(recent.buffers[recent._key(scope)])
        self.assertEqual(rows[0]["author_user_id"], "99")
        self.assertEqual(rows[0]["reply_target_user_id"], "100")
        self.assertEqual(rows[1]["author_user_id"], "99")
        self.assertIsNone(rows[1]["reply_target_user_id"])
