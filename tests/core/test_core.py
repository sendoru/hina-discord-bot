import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from hina_bot.core.config import parse_call_prefixes
from hina_bot.core.routing import Scope, chunks, trigger_text
from hina_bot.core.store import Store


def message(text="", *, mentions=(), bot=False, webhook=None, dm=False, reference=None):
    return NS(content=text, author=NS(bot=bot), mentions=[NS(id=x) for x in mentions],
              webhook_id=webhook, guild=None if dm else NS(id=1), reference=reference)


class RoutingTests(unittest.TestCase):
    def test_keyword_only_at_start(self):
        self.assertEqual(trigger_text(message("  히나야, 안녕"), 99), "히나야, 안녕")
        self.assertIsNone(trigger_text(message("안녕 히나야"), 99))
        self.assertEqual(trigger_text(message("히나야안녕"), 99), "히나야안녕")
        self.assertEqual(trigger_text(message("히나야"), 99), "")
        self.assertEqual(trigger_text(message("히나야!"), 99), "")

    def test_configurable_prefixes_and_overlapping_match(self):
        prefixes = parse_call_prefixes("히나, 히나야, 히나쨩, 히나")
        self.assertEqual(prefixes, ("히나", "히나야", "히나쨩"))
        self.assertEqual(trigger_text(message("히나쨩! 안녕"), 99, prefixes=prefixes), "히나쨩! 안녕")
        self.assertEqual(trigger_text(message("히나야안녕"), 99, prefixes=prefixes), "히나야안녕")
        self.assertIsNone(trigger_text(message("안녕 히나쨩"), 99, prefixes=prefixes))

    def test_invalid_prefix_configuration(self):
        with self.assertRaises(ValueError):
            parse_call_prefixes(" , ")
        with self.assertRaises(ValueError):
            parse_call_prefixes("x" * 33)

    def test_direct_mentions(self):
        self.assertEqual(trigger_text(message("안녕 <@99>", mentions=[99]), 99), "안녕")
        self.assertEqual(trigger_text(message("<@!99> 안녕", mentions=[99]), 99), "안녕")
        self.assertEqual(
            trigger_text(message("<@99>, 안녕, <@!99>!", mentions=[99]), 99),
            "안녕",
        )
        self.assertEqual(
            trigger_text(message("아코랑 <@99> 중에 누가 더 바빠?", mentions=[99]), 99),
            "아코랑 <@99> 중에 누가 더 바빠?",
        )
        self.assertIsNone(trigger_text(message("@everyone 안녕"), 99))
        self.assertIsNone(trigger_text(message("<@77>", mentions=[77]), 99))

    def test_reply_ping_toggle(self):
        self.assertEqual(trigger_text(message("안녕", mentions=[99], reference=NS()), 99), "안녕")
        self.assertIsNone(trigger_text(message("안녕", reference=NS()), 99))

    def test_bot_and_webhook_never_trigger(self):
        self.assertIsNone(trigger_text(message("히나야 안녕", bot=True), 99))
        self.assertIsNone(trigger_text(message("히나야 안녕", webhook=1), 99))

    def test_dm_config(self):
        self.assertIsNone(trigger_text(message("안녕", dm=True), 99))
        self.assertEqual(trigger_text(message("안녕", dm=True), 99, True), "안녕")
        self.assertEqual(trigger_text(message("히나야 안녕", dm=True), 99), "히나야 안녕")

    def test_configured_prefixes_are_preserved_except_for_bare_calls(self):
        prefixes = ("assistant", "assistant-bot")
        for dm, always_reply in ((True, True), (True, False), (False, True)):
            self.assertEqual(
                trigger_text(message("assistant-bot, hello", dm=dm), 99, always_reply, prefixes),
                "assistant-bot, hello",
            )
        self.assertEqual(
            trigger_text(
                message("<@99> assistant, hello", mentions=[99], dm=True),
                99,
                True,
                prefixes,
            ),
            "assistant, hello",
        )
        self.assertEqual(trigger_text(message("assistant-bot"), 99, prefixes=prefixes), "")
        self.assertEqual(
            trigger_text(message("<@99> assistant!", mentions=[99]), 99, prefixes=prefixes),
            "",
        )

    def test_ping_only(self):
        self.assertEqual(trigger_text(message("<@99>", mentions=[99]), 99), "")

    def test_unicode_chunks_preserve_content(self):
        text = "안녕하세요😀\n" * 1000
        parts = list(chunks(text))
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(p.encode("utf-16-le")) // 2 <= 1900 for p in parts))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:", history_turns=4)
        self.a = Scope(1, 10, 100, True)
        self.other_user = Scope(1, 10, 200, True)
        self.other_channel = Scope(1, 11, 100, True)
        self.other_server = Scope(2, 20, 100, True)
        self.dm = Scope(None, 30, 100)

    def tearDown(self):
        self.store.close()

    def test_conversation_isolation(self):
        self.store.add(self.a, 1, "공개 취미", "응")
        for scope in [self.other_user, self.other_channel, self.other_server, self.dm]:
            self.assertEqual(self.store.history(scope), [])
        self.assertEqual(len(self.store.history(self.a)), 1)

    def test_user_notes_realm_scoped(self):
        self.store.set_note(self.a.user_note, "별명")
        self.assertEqual(self.store.note(self.other_channel.user_note), "별명")
        for scope in [self.other_user, self.other_server, self.dm]:
            self.assertEqual(self.store.note(scope.user_note), "")

    def test_bounded_retention_and_summary_checkpoint(self):
        for i in range(1, 10):
            self.store.add(self.a, i, str(i), "응")
        self.assertEqual([r["content"] for r in self.store.history(self.a)], ["6", "7", "8", "9"])
        self.store.save_summary(self.a, "기억", 8)
        self.assertEqual([r["content"] for r in self.store.pending(self.a)], ["9"])

    def test_forget_all_channels_only_same_user_and_realm(self):
        scopes = [self.a, self.other_channel, self.other_user, self.other_server, self.dm]
        for i, scope in enumerate(scopes, 1):
            self.store.add(scope, i, "test", "응")
            self.store.save_summary(scope, "기억", i)
            self.store.set_note(scope.user_note, "메모")
        self.store.set_note(self.a.realm, "공통")
        self.store.forget(self.a)
        for scope in [self.a, self.other_channel]:
            self.assertEqual(self.store.history(scope), [])
            self.assertEqual(self.store.summary(scope), ("", 0))
            self.assertEqual(self.store.note(scope.user_note), "메모")
        for scope in [self.other_user, self.other_server, self.dm]:
            self.assertEqual(len(self.store.history(scope)), 1)
        self.assertEqual(self.store.note(self.a.realm), "공통")

    def test_public_to_dm_only_same_user(self):
        for i, scope in enumerate([self.a, self.other_user, self.dm], 1):
            self.store.add(scope, i, f"text{i}", "응")
            self.store.add_shared_call(scope, i, "speaker", f"text{i}")
        candidates = self.store.public_candidates(100)
        self.assertEqual([s.conversation for s in candidates], [self.a.conversation])
        context = self.store.public_context(candidates)
        self.assertEqual(context[0]["recent_user_messages"], ["text1"])
        self.assertNotIn("text3", str(context))

    def test_identity_candidates_are_recent_bounded_and_keep_observed_names(self):
        older = Scope(1, 10, 200, True)
        newer = Scope(1, 11, 300, True)
        other_guild = Scope(2, 20, 400, True)
        self.store.add_shared_call(older, 1, "Tag : Sendol", "첫 발언")
        self.store.add_shared_call(older, 2, "sendol", "둘째 발언")
        self.store.add_shared_call(newer, 3, "manager_lulu", "최근 발언")
        self.store.add_shared_call(other_guild, 4, "outsider", "다른 서버")

        candidates = self.store.identity_candidates(1, exclude_user_ids={300})

        self.assertEqual([row["user_id"] for row in candidates], ["200"])
        self.assertEqual(candidates[0]["names"], ["sendol", "Tag : Sendol"])
        self.assertNotIn("outsider", str(candidates))

    def test_private_then_public_does_not_export_private_context(self):
        private = Scope(1, 10, 100, False)
        self.store.add(private, 1, "비공개", "비공개 답변")
        self.store.save_summary(private, "비밀 요약", 1)
        self.store.add(self.a, 2, "이제 공개", "이전 비밀에 대한 답변")
        self.assertEqual(self.store.public_candidates(100), [])
        context = self.store.public_context([self.a])
        self.assertEqual(context[0]["summary"], "")
        self.assertEqual(context[0]["recent_user_messages"], [])

    def test_public_then_private_only_exports_old_public_turn(self):
        self.store.add(self.a, 1, "공개", "응")
        self.store.add_shared_call(self.a, 1, "speaker", "공개")
        private = Scope(1, 10, 100, False)
        self.store.add(private, 2, "비공개", "응")
        self.store.save_summary(private, "섞인 요약", 2)
        context = self.store.public_context([self.a])[0]
        self.assertEqual(context["summary"], "")
        self.assertEqual(context["recent_user_messages"], ["공개"])

    def test_persistence_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "memory.db")
            first = Store(path)
            first.add(self.a, 1, "취미", "응")
            first.save_summary(self.a, "취미 기억", 1)
            first.close()
            second = Store(path)
            self.assertEqual(second.summary(self.a), ("취미 기억", 1))
            self.assertTrue(second.seen(1))
            second.close()


if __name__ == "__main__":
    unittest.main()


class SharedContextTests(unittest.TestCase):
    def test_recent_context_budget_order_and_channel_isolation(self):
        from hina_bot.core.recent import RecentMessages
        recent = RecentMessages(budget=8)
        a, b = Scope(1, 10, 100), Scope(1, 10, 200)
        recent.add(a, 1, "A", "12345")
        recent.add(b, 2, "B", "abcdef")
        rows = recent.context(b, 3)
        self.assertEqual([r["name"] for r in rows], ["A", "B"])
        self.assertEqual(sum(len(r["content"]) for r in rows), 8)
        self.assertEqual(rows[-1]["content"], "abcdef")
        self.assertEqual(recent.context(Scope(1, 20, 200), 3), [])
        self.assertEqual(recent.context(Scope(None, 10, 200), 3), [])
        self.assertEqual([r["name"] for r in recent.context(a, 2)], ["A"])
        recent.forget(a)
        self.assertEqual(recent.context(b, 3), [])

    def test_recent_ttl_and_capacity(self):
        from unittest.mock import patch

        from hina_bot.core.recent import RecentMessages
        recent = RecentMessages(ttl=5, channels=1)
        with patch("hina_bot.core.recent.time.monotonic", return_value=10):
            recent.add(Scope(1, 10, 100), 1, "A", "old")
            recent.add(Scope(1, 20, 100), 2, "A", "new")
        self.assertEqual(len(recent.buffers), 1)
        with patch("hina_bot.core.recent.time.monotonic", return_value=16):
            self.assertEqual(recent.context(Scope(1, 20, 100), 3), [])

    def test_shared_calls_cross_users_but_not_guilds_or_private(self):
        store = Store(":memory:")
        a, b = Scope(1, 10, 100, True), Scope(1, 20, 200, True)
        for i, source in enumerate([a, b, Scope(2, 30, 300, True),
                                    Scope(1, 40, 400, False), Scope(None, 50, 500)], 1):
            store.add_shared_call(source, i, "speaker", "called")
        self.assertEqual({s.user_id for s in store.public_candidates(200, 1)}, {100, 200})
        self.assertEqual({s.user_id for s in store.public_candidates(200)}, {200})
        store.save_shared_summary(a, "A", "A preference", 1)
        store.forget(a)
        self.assertEqual(store.shared_summary(a), ("", 0))
        self.assertEqual({s.user_id for s in store.public_candidates(200, 1)}, {200})
        store.close()
