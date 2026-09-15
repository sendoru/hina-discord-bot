import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry
from hina_bot.discord.knowledge_commands import KnowledgeCommands


class KnowledgeExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = AdminDatabase(":memory:")
        facts = RuntimeKnowledgeRegistry(self.database, kind="world_fact")
        contexts = RuntimeKnowledgeRegistry(self.database, kind="interpretation")
        self.long_content = "긴 knowledge 본문 " + ("가나다라마바사" * 220)
        facts.add(
            "long.fact",
            self.long_content,
            "긴본문,테스트",
            "히나",
            "self",
            "상시",
        )
        self.group = KnowledgeCommands(NS(
            emoji_admin_ids={100},
            llm=NS(runtime_lore=facts, story_context=contexts),
        ))

    async def asyncTearDown(self):
        self.database.close()

    @staticmethod
    def _exported(send_message: AsyncMock) -> tuple[str, str]:
        attachment = send_message.call_args.kwargs["file"]
        attachment.fp.seek(0)
        text = attachment.fp.read().decode("utf-8")
        filename = attachment.filename
        attachment.close()
        return text, filename

    async def test_list_exports_full_knowledge_content(self):
        interaction = NS(response=NS(send_message=AsyncMock()))

        await self.group.list_items.callback(self.group, interaction, None, "time")

        exported, filename = self._exported(interaction.response.send_message)
        self.assertEqual(filename, "knowledge.txt")
        self.assertIn("long.fact", exported)
        self.assertIn(self.long_content, exported)
        self.assertIn("created_at:", exported)
        self.assertIn("updated_at:", exported)
        self.assertIn(" UTC", exported)
        self.assertNotIn("<t:", exported)
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_show_exports_full_knowledge_content(self):
        interaction = NS(response=NS(send_message=AsyncMock()))

        await self.group.show.callback(self.group, interaction, "long.fact")

        exported, filename = self._exported(interaction.response.send_message)
        self.assertEqual(filename, "knowledge-long.fact.txt")
        self.assertIn("long.fact", exported)
        self.assertIn(self.long_content, exported)
        self.assertIn("awareness: self", exported)
        self.assertIn("timeline: 상시", exported)


if __name__ == "__main__":
    unittest.main()
