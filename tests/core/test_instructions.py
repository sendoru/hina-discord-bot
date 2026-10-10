import tempfile
import unittest
from pathlib import Path

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.instructions import InstructionRegistry


class InstructionRegistryTests(unittest.TestCase):
    def test_crud_and_active_text(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AdminDatabase(str(Path(directory) / "hina.sqlite3"))
            registry = InstructionRegistry(database)

            self.assertEqual(registry.list(), [])
            self.assertEqual(registry.active_text(), "")

            registry.add("meta_guard", "메타 질문에도 세계 안의 히나로 답하세요.")
            registry.add("restraint", "말줄임표를 반복하지 마세요.")
            rows = registry.list()
            self.assertEqual([row["id"] for row in rows], ["meta_guard", "restraint"])
            self.assertTrue(all(row["enabled"] for row in rows))
            self.assertTrue(all(row.get("created_at") for row in rows))
            self.assertTrue(all(row.get("updated_at") for row in rows))
            self.assertIn("meta_guard", registry.active_text())
            self.assertIn("restraint", registry.active_text())

            registry.set_enabled("meta_guard", False)
            active = registry.active_text()
            self.assertNotIn("meta_guard", active)
            self.assertIn("restraint", active)

            registry.edit("restraint", "한숨과 말줄임표를 습관적으로 반복하지 마세요.")
            self.assertIn("한숨과 말줄임표", registry.active_text())

            registry.remove("meta_guard")
            self.assertEqual([row["id"] for row in registry.list()], ["restraint"])
            database.close()

    def test_validation_and_disabled_registry(self):
        registry = InstructionRegistry(None)
        self.assertEqual(registry.list(), [])
        self.assertEqual(registry.active_text(), "")
        with self.assertRaises(ValueError):
            registry.add("valid-id", "저장할 수 없어야 합니다.")

        with tempfile.TemporaryDirectory() as directory:
            database = AdminDatabase(str(Path(directory) / "hina.sqlite3"))
            registry = InstructionRegistry(database)
            for identifier in ("a", "한글", "bad id", "UPPER CASE"):
                with self.assertRaises(ValueError):
                    registry.add(identifier, "내용")
            with self.assertRaises(ValueError):
                registry.add("valid-id", "")
            with self.assertRaises(ValueError):
                registry.add("valid-id", "가" * 1201)
            registry.add("valid-id", "내용")
            with self.assertRaises(ValueError):
                registry.add("valid-id", "중복")
            with self.assertRaises(ValueError):
                registry.remove("missing-id")
            database.close()

    def test_legacy_import_keeps_unknown_created_at_null(self):
        database = AdminDatabase(":memory:")
        registry = InstructionRegistry(database)
        registry.import_row({"id": "legacy-rule", "text": "예전 규칙", "enabled": True})
        row = registry.get("legacy-rule")
        self.assertIsNone(row["created_at"])
        self.assertIsNone(row["updated_at"])
        database.close()


if __name__ == "__main__":
    unittest.main()
