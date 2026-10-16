import sqlite3
from contextlib import closing

import pytest

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.instructions import InstructionRegistry
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry


def test_registry_writes_join_outer_admin_transaction(tmp_path):
    path = tmp_path / "admin.sqlite3"
    database = AdminDatabase(str(path))
    instructions = InstructionRegistry(database)
    knowledge = RuntimeKnowledgeRegistry(database, kind="world_fact")
    try:
        with closing(sqlite3.connect(path)) as observer:
            with pytest.raises(RuntimeError, match="abort import"), database.transaction():
                instructions.add("test.rule", "keep this rule")
                knowledge.add("test.fact", "a fact", "test", "hina", "public_knowledge", "now")
                assert observer.execute("SELECT COUNT(*) FROM instructions").fetchone() == (0,)
                assert observer.execute("SELECT COUNT(*) FROM runtime_knowledge").fetchone() == (0,)
                raise RuntimeError("abort import")
            assert instructions.list() == []
            assert knowledge.list() == []
            # Registry operations still work and commit independently afterwards.
            instructions.add("test.rule", "keep this rule")
            assert observer.execute("SELECT text FROM instructions").fetchone() == ("keep this rule",)
    finally:
        database.close()
