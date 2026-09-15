import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.instructions import InstructionRegistry
from hina_bot.discord.instruction_commands import InstructionCommands


class InstructionExportFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.database = AdminDatabase(":memory:")
        self.registry = InstructionRegistry(self.database)
        self.registry.add("warm-tone", "평상시에는 상대를 편하게 대합니다.")
        self.group = InstructionCommands(NS(
            llm=NS(instructions=self.registry),
            emoji_admin_ids={100},
        ))

    def tearDown(self):
        self.database.close()

    @staticmethod
    def exported(send_message: AsyncMock) -> tuple[str, str]:
        attachment = send_message.call_args.kwargs["file"]
        attachment.fp.seek(0)
        text = attachment.fp.read().decode("utf-8")
        filename = attachment.filename
        attachment.close()
        return text, filename

    def test_command_surface_has_show(self):
        self.assertEqual(
            {command.name for command in self.group.commands},
            {"add", "list", "show", "edit", "enable", "disable", "remove"},
        )

    async def test_list_uses_readable_timestamps(self):
        interaction = NS(response=NS(send_message=AsyncMock()))
        await self.group.list_items.callback(self.group, interaction, None, "time")
        text, filename = self.exported(interaction.response.send_message)

        self.assertEqual(filename, "instructions.txt")
        self.assertIn("created_at:", text)
        self.assertIn("updated_at:", text)
        self.assertIn(" UTC", text)
        self.assertNotIn("<t:", text)
        self.assertIn("평상시에는 상대를 편하게 대합니다.", text)

    async def test_show_exports_named_single_item_file(self):
        interaction = NS(response=NS(send_message=AsyncMock()))
        await self.group.show.callback(self.group, interaction, "warm-tone")
        text, filename = self.exported(interaction.response.send_message)

        self.assertEqual(filename, "instruction-warm-tone.txt")
        self.assertIn("[warm-tone] ON", text)
        self.assertIn("평상시에는 상대를 편하게 대합니다.", text)


if __name__ == "__main__":
    unittest.main()
