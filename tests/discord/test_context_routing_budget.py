import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.config import Settings
from hina_bot.routing import Scope
from hina_bot.store import Store

from hina_bot.discord.reply_context import REPLY_CONTEXT
from hina_bot.discord.target_context import TARGET_CONTEXT
from hina_bot.discord.target_recent import TargetAwareRecentMessages
from hina_bot.discord.web_bot import HinaClient, _public_context_request


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_assistant_filter_runs_before_recent_item_limit(self):
        recent = TargetAwareRecentMessages(limit=40, budget=6000, store=self.store)
        user_a = Scope(1, 10, 100)
        user_b = Scope(1, 10, 200)

        for message_id in range(1, 13):
            recent.add(user_b, message_id, "B", f"B의 유효한 과거 발언 {message_id}")
        for message_id in range(13, 25):
            recent.add(user_a, message_id, "히나", f"A에게 한 답변 {message_id}", role="assistant")

        rows = recent.context(user_b, 99)
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(row["role"] == "user" for row in rows))
        self.assertEqual(rows[0]["message_id"], 1)
        self.assertEqual(rows[-1]["message_id"], 12)

    def test_busy_channel_keeps_same_speaker_thread_and_some_ambient_chat(self):
        recent = TargetAwareRecentMessages(limit=40, budget=6000, store=self.store)
        speaker = Scope(1, 10, 200)
        other = Scope(1, 10, 300)

        recent.add(speaker, 1, "B", "히나야 메이드복 입은 거 보고 싶어")
        recent.add(speaker, 2, "히나", "그런 옷을 입어 달라는 건 좀 곤란해.", role="assistant")
        recent.add(speaker, 3, "B", "장난 아닌데")
        recent.add(speaker, 4, "히나", "그래도 지금은 싫어.", role="assistant")
        for message_id in range(5, 21):
            recent.add(other, message_id, "다른 사람", f"끼어든 짧은 채팅 {message_id}")

        rows = recent.context(speaker, 99)
        contents = [row["content"] for row in rows]
        kinds = [row.get("context_kind") for row in rows]

        self.assertIn("히나야 메이드복 입은 거 보고 싶어", contents)
        self.assertIn("장난 아닌데", contents)
        self.assertIn("그래도 지금은 싫어.", contents)
        self.assertIn("speaker_thread", kinds)
        self.assertIn("channel_ambient", kinds)
        self.assertLessEqual(len(rows), 18)

    def test_cross_user_assistant_tone_is_still_excluded_from_busy_context(self):
        recent = TargetAwareRecentMessages(limit=40, budget=6000, store=self.store)
        user_a = Scope(1, 10, 100)
        user_b = Scope(1, 10, 200)
        other = Scope(1, 10, 300)

        recent.add(user_b, 1, "B", "내 얘기는 기억해 줘")
        recent.add(user_b, 2, "히나", "응, 그 얘기 말이지.", role="assistant")
        for message_id in range(3, 13):
            recent.add(other, message_id, "다른 사람", f"주변 대화 {message_id}")
        recent.add(user_a, 13, "히나", "A한테만 한 날 선 답변", role="assistant")

        rows = recent.context(user_b, 99)
        contents = [row["content"] for row in rows]
        self.assertIn("내 얘기는 기억해 줘", contents)
        self.assertIn("응, 그 얘기 말이지.", contents)
        self.assertNotIn("A한테만 한 날 선 답변", contents)

    def test_all_channel_context_sources_share_one_budget(self):
        recent = TargetAwareRecentMessages(limit=30, budget=100, store=self.store)
        scope = Scope(1, 10, 200)
        recent.add(scope, 1, "B", "현재 채널 문맥 A" * 8)
        recent.add(scope, 2, "B", "현재 채널 문맥 B" * 8)

        reply_token = REPLY_CONTEXT.set(({
            "message_id": "90",
            "user_id": "300",
            "name": "답장 대상",
            "content": "명시적으로 답장한 메시지" * 6,
            "role": "user",
        },))
        target_token = TARGET_CONTEXT.set(({
            "user_id": "400",
            "name": "언급 대상",
            "sampled_messages": [{
                "message_id": "80",
                "at": "2026-09-14T16:00:00+09:00",
                "content": "대상 사용자의 예전 발언" * 8,
            }],
        },))
        try:
            rows = recent.context(scope, 99)
        finally:
            TARGET_CONTEXT.reset(target_token)
            REPLY_CONTEXT.reset(reply_token)

        self.assertLessEqual(sum(len(row["content"]) for row in rows), 100)
        self.assertLessEqual(len(rows), 18)
        self.assertTrue(any(row.get("context_kind") == "replied_message" for row in rows))
        self.assertTrue(any(row.get("context_kind") == "target_user_history" for row in rows))
        self.assertTrue(any(row.get("context_kind") == "speaker_thread" for row in rows))


class PublicContextRoutingTests(unittest.TestCase):
    def test_ordinary_chat_does_not_load_public_memory(self):
        scope = Scope(1, 10, 100)
        self.assertEqual(_public_context_request(scope, "배고파", []), (False, ()))

    def test_current_user_memory_question_only_loads_current_user(self):
        scope = Scope(1, 10, 100)
        self.assertEqual(
            _public_context_request(scope, "내가 전에 뭐라고 했지?", []),
            (True, (100,)),
        )

    def test_explicit_target_only_loads_that_users_public_memory(self):
        scope = Scope(1, 10, 100)
        sampled = [{"user_id": "200", "name": "B", "sampled_messages": []}]
        enabled, user_ids = _public_context_request(scope, "B는 어떤 사람이야?", sampled)
        self.assertTrue(enabled)
        self.assertEqual(set(user_ids), {200})

    def test_broad_server_history_query_can_fan_out(self):
        scope = Scope(1, 10, 100)
        self.assertEqual(
            _public_context_request(scope, "이 서버에서 누가 전에 그런 말 했어?", []),
            (True, None),
        )


class FakeHistoryChannel:
    def __init__(self, messages):
        self.messages = messages

    def history(self, **kwargs):
        async def rows():
            for message in self.messages:
                yield message
        return rows()


class HydrationTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_hydration_does_not_store_untargeted_old_hina_replies(self):
        now = datetime.now(UTC)
        channel = FakeHistoryChannel([
            self.old_message(1, "최근 사용자 발언", now - timedelta(minutes=3)),
            self.old_message(2, "누구에게 했는지 모르는 히나 답변", now - timedelta(minutes=2),
                             author_id=99, bot=True),
        ])
        current = NS(id=10, created_at=now, channel=channel)
        scope = Scope(1, 10, 200, True)

        await self.bot.hydrate_recent_history(current, scope)

        raw_rows = list(self.bot.recent.buffers[self.bot.recent._key(scope)])
        self.assertEqual([row["content"] for row in raw_rows], ["최근 사용자 발언"])
        self.assertEqual([row["role"] for row in raw_rows], ["user"])
