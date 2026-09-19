import tempfile
import unittest
from pathlib import Path

from hina_bot.core.memory_items import (
    MemoryAccess,
    MemoryDisclosure,
    MemoryKind,
    memory_access,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


class StructuredMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.guild_a = Scope(1, 10, 100, True)
        self.guild_a_sibling = Scope(1, 11, 100, True)
        self.guild_a_private = Scope(1, 12, 100, False)
        self.guild_a_other_user = Scope(1, 10, 200, True)
        self.guild_b = Scope(2, 20, 100, True)
        self.dm = Scope(None, 30, 100)

    def tearDown(self):
        self.store.close()

    def test_memory_item_round_trip_preserves_provenance(self):
        item_id = self.store.add_memory_item(
            self.guild_a,
            "  다음 주에 면접이 있다  ",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
            source_message_ids=(123, "123", 456),
            confidence=0.8,
        )

        items = self.store.memory_items(100)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.id, item_id)
        self.assertEqual(item.content, "다음 주에 면접이 있다")
        self.assertEqual(item.kind, MemoryKind.EVENT)
        self.assertEqual(item.origin_realm, self.guild_a.realm)
        self.assertEqual(item.origin_channel_id, str(self.guild_a.channel_id))
        self.assertTrue(item.origin_public_at_capture)
        self.assertEqual(item.disclosure, MemoryDisclosure.REFERENCE_GATED)
        self.assertEqual(item.source_message_ids, ("123", "456"))
        self.assertAlmostEqual(item.confidence, 0.8)

    def test_private_origin_visibility_is_preserved(self):
        self.store.add_memory_item(
            self.guild_a_private,
            "private",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        item = self.store.memory_items(100)[0]
        self.assertFalse(item.origin_public_at_capture)
        self.assertEqual(item.origin_channel_id, str(self.guild_a_private.channel_id))

    def test_memory_item_validation_fails_closed(self):
        with self.assertRaises(ValueError):
            self.store.add_memory_item(
                self.dm,
                "   ",
                kind=MemoryKind.FACT,
                disclosure=MemoryDisclosure.LOCAL,
            )
        with self.assertRaises(ValueError):
            self.store.add_memory_item(
                self.dm,
                "fact",
                kind="unknown",
                disclosure=MemoryDisclosure.LOCAL,
            )
        with self.assertRaises(ValueError):
            self.store.add_memory_item(
                self.dm,
                "fact",
                kind=MemoryKind.FACT,
                disclosure="unknown",
            )
        with self.assertRaises(ValueError):
            self.store.add_memory_item(
                self.dm,
                "fact",
                kind=MemoryKind.FACT,
                disclosure=MemoryDisclosure.LOCAL,
                confidence=1.1,
            )

    def test_items_can_be_filtered_by_origin_realm(self):
        self.store.add_memory_item(
            self.guild_a,
            "guild fact",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        self.store.add_memory_item(
            self.dm,
            "dm fact",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        guild_items = self.store.memory_items(100, origin_realm=self.guild_a.realm)
        self.assertEqual([item.content for item in guild_items], ["guild fact"])

    def test_structured_items_persist_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "memory.db")
            first = Store(path)
            first.add_memory_item(
                self.dm,
                "persistent",
                kind=MemoryKind.PREFERENCE,
                disclosure=MemoryDisclosure.GLOBAL,
                source_message_ids=(999,),
            )
            first.close()

            second = Store(path)
            items = second.memory_items(100)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].content, "persistent")
            self.assertEqual(items[0].source_message_ids, ("999",))
            second.close()

    def test_existing_forget_and_purge_paths_cover_structured_items(self):
        self.store.add_memory_item(
            self.guild_a,
            "a-user-100",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        self.store.add_memory_item(
            self.guild_a_sibling,
            "a-sibling-user-100",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        self.store.add_memory_item(
            self.guild_a_other_user,
            "a-user-200",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        self.store.add_memory_item(
            self.guild_b,
            "b-user-100",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        self.store.add_memory_item(
            self.dm,
            "dm-user-100",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )

        self.store.forget(self.guild_a)
        remaining = {item.content for item in self.store.memory_items(100)}
        self.assertEqual(remaining, {"b-user-100", "dm-user-100"})
        self.assertEqual(
            [item.content for item in self.store.memory_items(200)],
            ["a-user-200"],
        )

        self.store.purge_channel_memory(self.guild_a)
        self.assertEqual(self.store.memory_items(200), [])
        self.store.purge_all_memory()
        self.assertEqual(self.store.memory_items(100), [])


class StructuredMemoryPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.guild_a = Scope(1, 10, 100, True)
        self.guild_a_sibling = Scope(1, 11, 100, True)
        self.guild_a_private = Scope(1, 12, 100, False)
        self.guild_a_other_private = Scope(1, 13, 100, False)
        self.guild_b = Scope(2, 20, 100, True)
        self.dm = Scope(None, 30, 100)
        self.other_user_guild = Scope(1, 10, 200, True)

    def tearDown(self):
        self.store.close()

    def item(self, origin: Scope, disclosure: MemoryDisclosure):
        self.store.add_memory_item(
            origin,
            f"{origin.realm}:{origin.channel_id}:{disclosure.value}",
            kind=MemoryKind.FACT,
            disclosure=disclosure,
        )
        return self.store.memory_items(origin.user_id)[-1]

    def test_local_memory_never_crosses_disclosure_spaces(self):
        public_item = self.item(self.guild_a, MemoryDisclosure.LOCAL)
        self.assertEqual(memory_access(public_item, self.guild_a_sibling), MemoryAccess.FULL)
        self.assertEqual(memory_access(public_item, self.guild_b), MemoryAccess.HIDDEN)
        self.assertEqual(memory_access(public_item, self.dm), MemoryAccess.FULL)

        private_item = self.item(self.guild_a_private, MemoryDisclosure.LOCAL)
        self.assertEqual(memory_access(private_item, self.guild_a_private), MemoryAccess.FULL)
        self.assertEqual(memory_access(private_item, self.guild_a), MemoryAccess.HIDDEN)
        self.assertEqual(memory_access(private_item, self.guild_a_other_private), MemoryAccess.HIDDEN)
        self.assertEqual(memory_access(private_item, self.dm), MemoryAccess.FULL)

        dm_item = self.item(self.dm, MemoryDisclosure.LOCAL)
        self.assertEqual(memory_access(dm_item, self.dm), MemoryAccess.FULL)
        self.assertEqual(memory_access(dm_item, self.guild_a), MemoryAccess.HIDDEN)

    def test_implicit_memory_crosses_spaces_as_attitude_only(self):
        item = self.item(self.dm, MemoryDisclosure.IMPLICIT)
        self.assertEqual(memory_access(item, self.dm), MemoryAccess.FULL)
        self.assertEqual(memory_access(item, self.guild_a), MemoryAccess.IMPLICIT)
        self.assertEqual(memory_access(item, self.guild_b), MemoryAccess.IMPLICIT)

        private_item = self.item(self.guild_a_private, MemoryDisclosure.IMPLICIT)
        self.assertEqual(memory_access(private_item, self.guild_a), MemoryAccess.IMPLICIT)
        self.assertEqual(memory_access(private_item, self.dm), MemoryAccess.FULL)

    def test_reference_gated_dm_memory_requires_owner_reference_in_server(self):
        item = self.item(self.dm, MemoryDisclosure.REFERENCE_GATED)
        self.assertEqual(memory_access(item, self.guild_a), MemoryAccess.HIDDEN)
        self.assertEqual(
            memory_access(item, self.guild_a, explicitly_referenced=True),
            MemoryAccess.FULL,
        )

    def test_reference_gated_server_memory_is_not_shared_between_servers(self):
        item = self.item(self.guild_a, MemoryDisclosure.REFERENCE_GATED)
        self.assertEqual(memory_access(item, self.guild_a_sibling), MemoryAccess.FULL)
        self.assertEqual(memory_access(item, self.guild_b), MemoryAccess.HIDDEN)
        self.assertEqual(
            memory_access(item, self.guild_b, explicitly_referenced=True),
            MemoryAccess.FULL,
        )

    def test_private_server_memory_stays_gated_even_inside_same_guild(self):
        item = self.item(self.guild_a_private, MemoryDisclosure.REFERENCE_GATED)
        self.assertEqual(memory_access(item, self.guild_a_private), MemoryAccess.FULL)
        self.assertEqual(memory_access(item, self.guild_a), MemoryAccess.HIDDEN)
        self.assertEqual(memory_access(item, self.guild_a_other_private), MemoryAccess.HIDDEN)
        self.assertEqual(
            memory_access(item, self.guild_a, explicitly_referenced=True),
            MemoryAccess.FULL,
        )

    def test_owner_dm_aggregates_reference_gated_memory_from_every_origin(self):
        public_item = self.item(self.guild_a, MemoryDisclosure.REFERENCE_GATED)
        self.assertEqual(memory_access(public_item, self.dm), MemoryAccess.FULL)
        self.assertEqual(
            memory_access(public_item, self.dm, public_server_memory_in_dm=False),
            MemoryAccess.FULL,
        )

        private_item = self.item(self.guild_a_private, MemoryDisclosure.REFERENCE_GATED)
        self.assertEqual(memory_access(private_item, self.dm), MemoryAccess.FULL)
        self.assertEqual(
            memory_access(private_item, self.dm, public_server_memory_in_dm=False),
            MemoryAccess.FULL,
        )

    def test_owner_dm_never_aggregates_another_users_memory(self):
        self.store.add_memory_item(
            self.other_user_guild,
            "other-user-private-fact",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        item = self.store.memory_items(200)[-1]
        self.assertEqual(memory_access(item, self.dm), MemoryAccess.HIDDEN)

    def test_global_memory_can_cross_spaces_but_never_cross_users(self):
        item = self.item(self.dm, MemoryDisclosure.GLOBAL)
        self.assertEqual(memory_access(item, self.guild_a), MemoryAccess.FULL)
        self.assertEqual(memory_access(item, self.guild_b), MemoryAccess.FULL)
        self.assertEqual(
            memory_access(item, self.other_user_guild, explicitly_referenced=True),
            MemoryAccess.HIDDEN,
        )


if __name__ == "__main__":
    unittest.main()
