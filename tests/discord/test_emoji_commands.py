import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

from hina_bot.core.store import Store
from hina_bot.discord.emoji_commands import EmojiCommands, EmojiRegistry


class Emoji:
    id = 1234
    guild = NS(id=100, me=NS())

    def is_usable(self):
        return True

    def __str__(self):
        return "<:original_name:1234>"


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.client = NS(get_emoji=MagicMock(return_value=Emoji()),
                         create_application_emoji=AsyncMock(return_value=Emoji()),
                         fetch_application_emojis=AsyncMock(return_value=[]))
        self.registry = EmojiRegistry(self.client, self.store)

    async def asyncTearDown(self):
        self.store.close()

    async def test_source_registration_and_permissions(self):
        await self.registry.add("hina_happy", "기쁠 때", source="<:히나웃음:1234>")
        self.client.create_application_emoji.assert_not_awaited()
        same = NS(guild=NS(id=100, me=NS()))
        self.assertEqual((await self.registry.catalog(same))[0]["name"], "hina_happy")
        other = NS(guild=NS(id=200, me=NS()),
                   permissions_for=lambda _: NS(external_emojis=False))
        self.assertEqual(await self.registry.catalog(other), [])
        other.permissions_for = lambda _: NS(external_emojis=True)
        self.assertEqual(len(await self.registry.catalog(other)), 1)
        self.assertEqual(len(await self.registry.catalog()), 1)
        self.client.get_emoji.return_value = None
        self.assertEqual(await self.registry.catalog(same), [])

    async def test_edit_remove_does_not_delete_source(self):
        await self.registry.add("hina_happy", "기쁨", source="1234")
        await self.registry.edit("hina_happy", "칭찬받았을 때")
        self.assertEqual(self.store.emoji_rows()[0]["description"], "칭찬받았을 때")
        await self.registry.remove("hina_happy")
        self.assertEqual(await self.registry.catalog(), [])

    async def test_duplicate_limit_and_bad_input(self):
        await self.registry.add("hina_happy", "기쁨", source="1234")
        with self.assertRaises(ValueError):
            await self.registry.add("hina_happy", "기쁨", source="1234")
        with self.assertRaises(ValueError):
            await self.registry.add("no space", "기쁨", source="1234")
        with self.assertRaises(ValueError):
            await self.registry.add("good_alias", "기쁨")
        for i in range(19):
            self.store.add_emoji(f"entry_{i}", str(2000 + i), "test", "100")
        with self.assertRaises(ValueError):
            await self.registry.add("twenty_one", "기쁨", source="9999")

    async def test_image_upload_validation(self):
        attachment = NS(size=8, read=AsyncMock(return_value=b"\x89PNG\r\n\x1a\n"))
        await self.registry.add("hina_blush", "부끄러움", attachment)
        self.client.create_application_emoji.assert_awaited_once()
        self.assertIsNone(self.store.emoji_rows()[0]["source_guild_id"])
        attachment.read.return_value = b"not an image"
        with self.assertRaises(ValueError):
            await self.registry.add("hina_bad", "bad", attachment)

    async def test_admin_authorization_and_message_crud(self):
        client = NS(emoji_admin_ids={100}, emoji_registry=self.registry, store=self.store)
        group = EmojiCommands(client)
        message = NS(author=NS(id=200), channel=None, attachments=[])
        self.assertIn("관리자", await group.handle(message, "등록 hina_happy 1234 기쁠 때"))
        self.assertEqual(self.store.emoji_rows(), [])
        message.author.id = 100
        self.assertIn("등록했어요", await group.handle(message, "등록 hina_happy 1234 기쁠 때"))
        self.assertIn("기쁠 때", await group.handle(message, "목록"))
        await group.handle(message, "수정 hina_happy 칭찬받았을 때")
        self.assertEqual(self.store.emoji_rows()[0]["description"], "칭찬받았을 때")
        await group.handle(message, "삭제 hina_happy")
        self.assertEqual(self.store.emoji_rows(), [])

    async def test_message_image_and_invalid_arguments(self):
        group = EmojiCommands(NS(emoji_admin_ids={100}, emoji_registry=self.registry, store=self.store))
        attachment = NS(size=8, read=AsyncMock(return_value=b"\x89PNG\r\n\x1a\n"))
        message = NS(author=NS(id=100), channel=None, attachments=[attachment])
        self.assertIn("동시에", await group.handle(message, "등록 hina_happy <:x:1234> happy"))
        attachment.read.assert_not_awaited()
        self.assertIn("등록했어요", await group.handle(message, "등록 hina_happy 기쁠 때"))
        self.assertEqual(await group.handle(message, "등록"), group.HELP)

    async def test_concurrent_duplicate_registration(self):
        import asyncio
        results = await asyncio.gather(
            self.registry.add("hina_happy", "happy", source="1234"),
            self.registry.add("hina_happy", "happy", source="1234"), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual(len(self.store.emoji_rows()), 1)

    async def test_registry_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "bot.db")
            one = Store(path)
            one.add_emoji("hina_happy", "1234", "happy", "100")
            one.close()
            two = Store(path)
            self.assertEqual(two.emoji_rows()[0]["description"], "happy")
            two.close()
