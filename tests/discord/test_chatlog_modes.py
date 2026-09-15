"""Recent channel-log controls are independent from persistent memory."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    AVAILABLE = True
except ModuleNotFoundError:
    AVAILABLE = False

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

if AVAILABLE:
    from hina_bot.core.config import Settings
    from hina_bot.discord.bot import HinaClient


@unittest.skipUnless(AVAILABLE, "Install project dependencies to test Discord adapters")
class ChatLogAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.llm = NS(
            answer=AsyncMock(return_value="안녕"),
            summarize=AsyncMock(),
            summarize_shared=AsyncMock(),
            close=AsyncMock(),
        )
        self.bot = HinaClient(Settings("test", "test", cooldown=0), store=self.store, llm=self.llm)
        self.bot._connection.user = NS(id=99)
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 10
        self.channel.send = AsyncMock(return_value=NS(id=1000))
        self.channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        self.channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        self.channel.permissions_for.return_value = NS(view_channel=True, read_message_history=True)
        self.guild = NS(id=1, default_role=NS(), unavailable=False, me=NS(), emojis=[])
        self.author = NS(id=100, bot=False, display_name="사용자",
                         guild_permissions=NS(manage_guild=False))

    async def asyncTearDown(self):
        await self.bot.close()

    def message(self, text: str, message_id: int):
        return NS(id=message_id, content=text, author=self.author, guild=self.guild,
                  channel=self.channel, mentions=[], webhook_id=None)

    async def test_chatlog_off_skips_collection_and_prompt_context_only(self):
        scope = Scope(1, 10, 100)
        self.store.set_chat_log_mode_override(scope.channel, "off")

        await self.bot.on_message(self.message("호출하지 않은 앞 대화", 1))
        await self.bot.on_message(self.message("히나야 지금 질문", 2))

        self.llm.answer.assert_awaited_once()
        self.assertEqual(self.llm.answer.call_args.kwargs["channel_context"], [])
        self.assertTrue(self.llm.answer.call_args.kwargs["use_memory"])
        self.assertEqual(self.bot.recent.context(scope, 9999), [])
        self.assertTrue(self.store.seen(2))

    async def test_chatlog_on_still_works_when_long_term_memory_is_off(self):
        scope = Scope(1, 10, 100)
        self.store.set_memory_mode(scope, "off")

        await self.bot.on_message(self.message("호출하지 않은 앞 대화", 1))
        await self.bot.on_message(self.message("히나야 지금 질문", 2))

        context = self.llm.answer.call_args.kwargs["channel_context"]
        self.assertEqual([row["content"] for row in context], ["호출하지 않은 앞 대화"])
        self.assertFalse(self.llm.answer.call_args.kwargs["use_memory"])
        self.assertEqual(self.store.history(scope), [])
