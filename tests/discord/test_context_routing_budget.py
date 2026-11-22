import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.reply_context import REPLY_CONTEXT
from hina_bot.discord.target_context import TARGET_CONTEXT
from hina_bot.discord.target_recent import TargetAwareRecentMessages
from hina_bot.discord.web_bot import HinaClient


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.store = Store.create(":memory:")

    def tearDown(self):
        self.store.close()

    def test_cross_user_hina_replies_remain_available_with_target_metadata(self):
        recent = TargetAwareRecentMessages(limit=40, budget=6000, store=self.store)
        user_a = Scope(1, 10, 100)
        user_b = Scope(1, 10, 200)

        recent.add(user_a, 1, "A", "히나야 A의 질문", direct_trigger=True)
        recent.add(
            user_a, 2, "히나", "A에게 한 답변", role="assistant",
            author_user_id=99, reply_target_user_id=100,
            capture_turn_provenance=True,
        )
        recent.add(user_b, 3, "B", "히나야 B의 질문", direct_trigger=True)
        recent.add(
            user_b, 4, "히나", "B에게 한 답변", role="assistant",
            author_user_id=99, reply_target_user_id=200,
            capture_turn_provenance=True,
        )

        rows = recent.context(user_b, 99)
        self.assertEqual(
            [row["content"] for row in rows],
            ["히나야 A의 질문", "A에게 한 답변", "히나야 B의 질문", "B에게 한 답변"],
        )
        answer_to_a = next(row for row in rows if row["content"] == "A에게 한 답변")
        self.assertEqual(answer_to_a["reply_target_user_id"], "100")
        self.assertEqual(answer_to_a["context_kind"], "channel_ambient")

    def test_busy_channel_keeps_same_speaker_thread_and_some_ambient_chat(self):
        recent = TargetAwareRecentMessages(limit=40, budget=6000, store=self.store)
        speaker = Scope(1, 10, 200)
        other = Scope(1, 10, 300)

        recent.add(speaker, 1, "B", "히나야 메이드복 입은 거 보고 싶어")
        recent.add(
            speaker, 2, "히나", "그런 옷을 입어 달라는 건 좀 곤란해.", role="assistant",
            author_user_id=99, reply_target_user_id=200,
            capture_turn_provenance=True,
        )
        recent.add(speaker, 3, "B", "장난 아닌데")
        recent.add(
            speaker, 4, "히나", "그래도 지금은 싫어.", role="assistant",
            author_user_id=99, reply_target_user_id=200,
            capture_turn_provenance=True,
        )
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

    def test_busy_channel_caps_ambient_text_without_clipping_speaker_thread(self):
        recent = TargetAwareRecentMessages(limit=40, budget=6000, store=self.store)
        speaker = Scope(1, 10, 200)
        other = Scope(1, 10, 300)

        recent.add(speaker, 100, "B", "S" * 2500)
        for message_id in range(101, 107):
            recent.add(other, message_id, "다른 사람", "A" * 1000)

        rows = recent.context(speaker, 999)
        speaker_chars = sum(
            len(row["content"])
            for row in rows
            if row.get("context_kind") == "speaker_thread"
        )
        ambient_chars = sum(
            len(row["content"])
            for row in rows
            if row.get("context_kind") == "channel_ambient"
        )

        self.assertEqual(speaker_chars, 2500)
        self.assertLessEqual(ambient_chars, 2000)
        self.assertGreater(ambient_chars, 0)
        self.assertLessEqual(sum(len(row["content"]) for row in rows), 6000)

    def test_cross_user_assistant_tone_remains_attributed_in_busy_context(self):
        recent = TargetAwareRecentMessages(limit=40, budget=6000, store=self.store)
        user_a = Scope(1, 10, 100)
        user_b = Scope(1, 10, 200)
        other = Scope(1, 10, 300)

        recent.add(user_b, 1, "B", "내 얘기는 기억해 줘")
        recent.add(
            user_b, 2, "히나", "응, 그 얘기 말이지.", role="assistant",
            author_user_id=99, reply_target_user_id=200,
            capture_turn_provenance=True,
        )
        for message_id in range(3, 13):
            recent.add(other, message_id, "다른 사람", f"주변 대화 {message_id}")
        recent.add(
            user_a, 13, "히나", "A한테만 한 날 선 답변", role="assistant",
            author_user_id=99, reply_target_user_id=100,
            capture_turn_provenance=True,
        )

        rows = recent.context(user_b, 99)
        contents = [row["content"] for row in rows]
        self.assertIn("내 얘기는 기억해 줘", contents)
        self.assertIn("응, 그 얘기 말이지.", contents)
        self.assertIn("A한테만 한 날 선 답변", contents)
        row = next(item for item in rows if item["content"] == "A한테만 한 날 선 답변")
        self.assertEqual(row["reply_target_user_id"], "100")
        self.assertEqual(row["context_kind"], "channel_ambient")

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


class PublicRecentContextTests(unittest.TestCase):
    def setUp(self):
        self.store = Store.create(":memory:")
        self.now = datetime(2026, 11, 22, 11, 31, tzinfo=UTC)
        self.since = (self.now - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")

    def tearDown(self):
        self.store.close()

    def add_call(self, guild, channel, user, message_id, text, reply, *, minutes):
        scope = Scope(guild, channel, user, True)
        self.store.add(scope, message_id, text, reply, name=f"user-{user}")
        at = (self.now - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        with self.store.db:
            # Store.add writes turns only; the completed Discord delivery path
            # also records the public call in shared_calls.
            self.store._conversations.write_shared_call(
                scope, message_id, f"user-{user}", text,
            )
            self.store.db.execute(
                "UPDATE shared_calls SET created_at=? WHERE message_id=?",
                (at, str(message_id)),
            )
        return scope

    def test_recent_own_calls_are_selected_without_query_keywords(self):
        own = self.add_call(1, 10, 100, 101, "서버의 헛소리", "그만해", minutes=8)
        self.add_call(1, 20, 200, 102, "다른 사람 이야기", "응", minutes=2)
        self.add_call(1, 30, 100, 103, "오래된 말", "오래된 대답", minutes=61)
        self.add_call(2, 40, 100, 104, "다른 서버 본인", "응", minutes=5)
        with self.store.db:
            self.store._conversations.write_shared_summary(
                own, "speaker", "시간 확인 불가능한 오래된 요약", 1,
            )

        sources = self.store.recent_public_candidates(100, None, since=self.since)
        self.assertEqual(
            [source.channel_id for source in sources], [40, 10],
        )
        context = self.store.recent_public_context(sources, since=self.since)
        self.assertEqual(len(context), 2)
        self.assertIn("서버의 헛소리", str(context))
        self.assertIn("그만해", str(context))
        self.assertNotIn("오래된 말", str(context))
        self.assertNotIn("다른 사람 이야기", str(context))
        self.assertNotIn("시간 확인 불가능한 오래된 요약", str(context))
        self.assertNotIn("summary", str(context))

    def test_server_scope_excludes_current_channel_and_other_guild(self):
        self.add_call(1, 10, 100, 201, "현재 채널", "여기 답변", minutes=4)
        self.add_call(1, 20, 100, 202, "다른 채널", "저기 답변", minutes=5)
        self.add_call(1, 30, 200, 203, "다른 사용자", "응", minutes=3)
        self.add_call(2, 40, 100, 204, "다른 서버", "응", minutes=2)

        sources = self.store.recent_public_candidates(
            100, 1, since=self.since, exclude_channel_id=10,
        )
        self.assertEqual([source.channel_id for source in sources], [20])
        context = self.store.recent_public_context(sources, since=self.since)
        self.assertIn("다른 채널", str(context))
        self.assertNotIn("현재 채널", str(context))

    def test_per_source_two_exchanges_and_total_character_budget(self):
        for i in range(3):
            self.add_call(1, 10, 100, 301 + i, f"user-{i}", f"reply-{i}", minutes=3 - i)
        sources = self.store.recent_public_candidates(100, None, since=self.since)
        context = self.store.recent_public_context(sources, since=self.since, max_chars=23)
        self.assertEqual(len(context), 1)
        exchanges = context[0]["recent_exchanges"]
        self.assertEqual([row["user"] for row in exchanges], ["user-1", "user-2"])
        self.assertLessEqual(
            sum(len(row["user"]) + len(row.get("hina", "")) for row in exchanges),
            23,
        )

    def test_public_window_caps_source_count_at_four(self):
        for i in range(5):
            self.add_call(1, 10 + i, 100, 401 + i, f"channel-{i}", "응", minutes=10 - i)
        sources = self.store.recent_public_candidates(100, None, since=self.since)
        context = self.store.recent_public_context(sources, since=self.since)
        self.assertEqual(len(sources), 5)
        self.assertEqual(len(context), 4)


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
        self.store = Store.create(":memory:")
        self.llm = NS(close=AsyncMock())
        self.bot = HinaClient(Settings(discord_token="test", openai_api_key="test", cooldown=0), store=self.store, llm=self.llm)
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

    async def test_hydration_restores_old_hina_replies_as_shared_channel_context(self):
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
        self.assertEqual(
            [row["content"] for row in raw_rows],
            ["최근 사용자 발언", "누구에게 했는지 모르는 히나 답변"],
        )
        self.assertEqual([row["role"] for row in raw_rows], ["user", "assistant"])
