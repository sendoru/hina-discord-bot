import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.instructions import InstructionRegistry
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry
from hina_bot.core.runtime_migration import migrate


class RuntimeMigrationTests(unittest.TestCase):
    def test_migrates_legacy_json_and_preserves_unknown_dates_as_null(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "hina.sqlite3"
            instructions_path = root / "instructions.json"
            facts_path = root / "runtime_lore.json"
            contexts_path = root / "contexts.json"

            instructions_path.write_text(json.dumps([
                {"id": "legacy-rule", "text": "예전 규칙", "enabled": True},
                {
                    "id": "dated-rule",
                    "text": "날짜 있는 규칙",
                    "enabled": False,
                    "created_at": "2026-09-01T01:02:03+00:00",
                },
            ], ensure_ascii=False), encoding="utf-8")
            facts_path.write_text(json.dumps([{
                "id": "legacy.fact",
                "content": "예전 사실",
                "keywords": ["예전", "사실"],
                "subjects": ["히나"],
                "awareness": "unknown",
                "timeline": "시점 미지정",
                "enabled": True,
            }], ensure_ascii=False), encoding="utf-8")
            contexts_path.write_text(json.dumps([{
                "id": "legacy.context",
                "content": "예전 해석",
                "keywords": ["예전", "해석"],
                "subjects": ["히나"],
                "awareness": "inference",
                "timeline": "시점 미지정",
                "enabled": True,
                "created_at": "2026-09-02T03:04:05+00:00",
            }], ensure_ascii=False), encoding="utf-8")

            args = NS(
                database=str(database_path),
                instructions=str(instructions_path),
                facts=str(facts_path),
                contexts=str(contexts_path),
                replace=False,
                dry_run=False,
            )
            counters = migrate(args)
            self.assertEqual(counters["instructions"]["added"], 2)
            self.assertEqual(counters["world_fact"]["added"], 1)
            self.assertEqual(counters["interpretation"]["added"], 1)

            database = AdminDatabase(str(database_path))
            instructions = InstructionRegistry(database)
            facts = RuntimeKnowledgeRegistry(database, kind="world_fact")
            contexts = RuntimeKnowledgeRegistry(database, kind="interpretation")
            self.assertIsNone(instructions.get("legacy-rule")["created_at"])
            self.assertEqual(
                instructions.get("dated-rule")["created_at"],
                "2026-09-01T01:02:03+00:00",
            )
            self.assertIsNone(facts.get("legacy.fact")["created_at"])
            self.assertEqual(
                contexts.get("legacy.context")["created_at"],
                "2026-09-02T03:04:05+00:00",
            )
            database.close()

            second = migrate(args)
            self.assertEqual(second["instructions"]["skipped"], 2)
            self.assertEqual(second["world_fact"]["skipped"], 1)
            self.assertEqual(second["interpretation"]["skipped"], 1)

    def test_dry_run_rolls_back_imported_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "instructions.json"
            source.write_text(json.dumps([
                {"id": "dry-run", "text": "저장되면 안 됨", "enabled": True}
            ], ensure_ascii=False), encoding="utf-8")
            args = NS(
                database=str(root / "hina.sqlite3"),
                instructions=str(source),
                facts=str(root / "missing-facts.json"),
                contexts=str(root / "missing-contexts.json"),
                replace=False,
                dry_run=True,
            )
            counters = migrate(args)
            self.assertEqual(counters["instructions"]["added"], 1)

            database = AdminDatabase(args.database)
            self.assertEqual(InstructionRegistry(database).list(), [])
            database.close()


if __name__ == "__main__":
    unittest.main()
