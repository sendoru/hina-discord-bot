import time
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from hina_bot.routing import Scope
from hina_bot.store import Store

from hina_bot.discord.chatlog_capture import (
    capture_mode,
    capture_mode_chain,
    set_capture_mode_override,
)
from hina_bot.discord.target_context import collect
from hina_bot.discord.target_recent import CURRENT_DIRECT_TRIGGER, TargetAwareRecentMessages


class CaptureModeTests(unittest.TestCase):
    def test_inherits_global_server_channel_with_all_default(self):
        store = Store(":memory:")
        scope = Scope(1, 10, 100)
        self.assertEqual(capture_mode(store, scope), "all")

        set_capture_mode_override(store, "global", "direct")
        self.assertEqual(capture_mode(store, scope), "direct")
        set_capture_mode_override(store, scope.realm, "all")
        self.assertEqual(capture_mode(store, scope), "all")
        set_capture_mode_override(store, scope.channel, "direct")
        chain = capture_mode_chain(store, scope)
        self.assertEqual(chain["effective"], "direct")
        self.assertEqual(chain["source"], "channel")

        set_capture_mode_override(store, scope.channel, None)
        self.assertEqual(capture_mode(store, scope), "all")
        store.close()

    def test_direct_recent_keeps_only_direct_users_and_assistant(self):
        store = Store(":memory:")
        scope = Scope(1, 10, 100)
        set_capture_mode_override(store, "global", "direct")
        recent = TargetAwareRecentMessages(store=store)

        recent.add(scope, 1, "A", "옆 대화")
        token = CURRENT_DIRECT_TRIGGER.set(True)
        try:
            recent.add(scope, 2, "A", "히나야 질문")
        finally:
            CURRENT_DIRECT_TRIGGER.reset(token)
        recent.add(scope, 3, "히나", "답변", role="assistant")

        rows = recent.context(scope, 99)
        self.assertEqual([row["content"] for row in rows], ["히나야 질문", "답변"])
        store.close()

    def test_assistant_tone_is_scoped_to_reply_target(self):
        store = Store(":memory:")
        recent = TargetAwareRecentMessages(store=store)
        user_a = Scope(1, 10, 100)
        user_b = Scope(1, 10, 200)
        bot_scope = Scope(1, 10, 999)

        recent.add(user_a, 1, "A", "히나야 또 놀릴 거야")
        recent.add(user_a, 2, "히나", "이제 그만해.", role="assistant")
        recent.add(
            bot_scope,
            3,
            "히나",
            "재시작 전 답변",
            role="assistant",
            unix_time=time.time(),
        )
        recent.add(user_b, 4, "B", "히나야 배고파")

        raw_rows = list(recent.buffers[recent._key(user_a)])
        live = next(row for row in raw_rows if row["message_id"] == 2)
        hydrated = next(row for row in raw_rows if row["message_id"] == 3)
        self.assertEqual(live["reply_target_user_id"], "100")
        self.assertIsNone(live["author_user_id"])
        self.assertEqual(live["user_id"], "")
        self.assertEqual(hydrated["author_user_id"], "999")
        self.assertIsNone(hydrated["reply_target_user_id"])
        self.assertEqual(hydrated["user_id"], "999")

        rows_a = recent.context(user_a, 99)
        rows_b = recent.context(user_b, 99)
        self.assertIn("이제 그만해.", [row["content"] for row in rows_a])
        self.assertNotIn("이제 그만해.", [row["content"] for row in rows_b])
        self.assertNotIn("재시작 전 답변", [row["content"] for row in rows_a])
        self.assertNotIn("재시작 전 답변", [row["content"] for row in rows_b])
        store.close()


class FakeHistoryChannel:
    def __init__(self, messages):
        self.messages = messages

    def history(self, **kwargs):
        async def rows():
            for message in self.messages:
                yield message
        return rows()


class TargetContextCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_mode_ignores_target_users_unaddressed_chat(self):
        now = datetime.now(UTC)
        guild = NS(id=1)
        target = NS(id=200, bot=False, display_name="대상", name="대상")
        bot = NS(id=99, bot=True, display_name="히나")
        ordinary = NS(
            id=1,
            content="그냥 옆에서 한 말",
            author=target,
            webhook_id=None,
            created_at=now - timedelta(minutes=2),
            mentions=[],
            guild=guild,
        )
        direct = NS(
            id=2,
            content="히나야 이건 직접 한 말",
            author=target,
            webhook_id=None,
            created_at=now - timedelta(minutes=1),
            mentions=[bot],
            guild=guild,
        )
        channel = FakeHistoryChannel([direct, ordinary])
        message = NS(
            id=10,
            content="히나야 <@200> 어떻게 생각해?",
            author=NS(id=100, bot=False),
            guild=guild,
            channel=channel,
            mentions=[target],
            created_at=now,
        )

        sampled = await collect(
            message,
            99,
            "<@200> 어떻게 생각해?",
            direct_only=True,
            call_prefixes=("히나야",),
        )

        self.assertEqual(len(sampled), 1)
        self.assertEqual(
            [row["content"] for row in sampled[0]["sampled_messages"]],
            ["히나야 이건 직접 한 말"],
        )
