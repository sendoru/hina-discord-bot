import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.db_connections import open_existing, open_readonly
from hina_bot.core.db_schema import database_signature, prepare_connection
from hina_bot.core.instructions import InstructionRegistry
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry

from .maintenance_backup import backup_schema_database

MIGRATION_NAME = "legacy-json-admin-v1"


class _DryRunRollback(Exception):
    pass


def _load_rows(path: str, label: str) -> list[dict]:
    if not path:
        return []
    source = Path(path)
    if not source.exists():
        return []
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON을 읽을 수 없습니다: {source}: {exc}") from exc
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError(f"{label} JSON은 객체 배열이어야 합니다: {source}")
    return data


def _count(result: str, counters: dict[str, int]) -> None:
    counters[result] = counters.get(result, 0) + 1


def migrate(args) -> dict[str, dict[str, int]]:
    sources = {
        "instructions": _load_rows(args.instructions, "instruction"),
        "world_fact": _load_rows(args.facts, "runtime lore"),
        "interpretation": _load_rows(args.contexts, "context"),
    }
    path = Path(args.database).expanduser()
    if not args.dry_run and not getattr(args, 'offline_confirmed', False):
        raise ValueError('Stop all database writers and pass --offline-confirmed')
    # All dry-run validation uses a disposable clone and the real registries.
    if args.dry_run:
        database = AdminDatabase.copy_to_memory(path if path.exists() else None)
    else:
        expected = None
        if path.exists():
            original = open_readonly(path)
            try:
                expected = database_signature(original)
            finally:
                original.close()
            # Includes admin-only/legacy files; no DDL is needed to back them up.
            backup_schema_database(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb'):
                pass
        db = open_existing(path)
        try:
            prepare_connection(db, ('admin',), expected_signature=expected)
            db.execute('PRAGMA journal_mode=WAL')
            database = AdminDatabase(db)
        except BaseException:
            db.close()
            raise
    expected_import = database_signature(database.db)
    instructions = InstructionRegistry(database)
    facts = RuntimeKnowledgeRegistry(database, kind="world_fact")
    contexts = RuntimeKnowledgeRegistry(database, kind="interpretation")
    counters = {
        "instructions": {},
        "world_fact": {},
        "interpretation": {},
    }
    try:
        try:
            with database.transaction():
                if database_signature(database.db) != expected_import:
                    raise RuntimeError("Database changed before import; refuse apply")
                for row in sources["instructions"]:
                    _count(instructions.import_row(row, replace=args.replace), counters["instructions"])
                for row in sources["world_fact"]:
                    _count(facts.import_row(row, replace=args.replace), counters["world_fact"])
                for row in sources["interpretation"]:
                    _count(contexts.import_row(row, replace=args.replace), counters["interpretation"])
                database.db.execute(
                    "INSERT OR REPLACE INTO admin_migrations(name,applied_at) "
                    "VALUES (?,CURRENT_TIMESTAMP)",
                    (MIGRATION_NAME,),
                )
                if args.dry_run:
                    raise _DryRunRollback
        except _DryRunRollback:
            pass
    finally:
        database.close()
    return counters


def parser() -> argparse.ArgumentParser:
    load_dotenv(Path.cwd() / ".env.local", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    root = argparse.ArgumentParser(
        description="기존 instruction/knowledge JSON을 hina SQLite DB로 이전합니다."
    )
    root.add_argument(
        "--database",
        default=os.getenv("DATABASE_PATH", "data/hina.sqlite3"),
        help="대상 SQLite 경로 (기본: DATABASE_PATH 또는 data/hina.sqlite3)",
    )
    root.add_argument(
        "--instructions",
        default=os.getenv("INSTRUCTION_PATH", "data/instructions.json"),
        help="기존 instruction JSON 경로",
    )
    root.add_argument(
        "--facts",
        default=os.getenv("RUNTIME_LORE_PATH", "data/runtime_lore.json"),
        help="기존 world_fact JSON 경로",
    )
    root.add_argument(
        "--contexts",
        default=os.getenv("CONTEXT_PATH", "data/contexts.json"),
        help="기존 interpretation JSON 경로",
    )
    root.add_argument(
        "--dry-run",
        action="store_true",
        help="검증과 충돌 확인만 하고 DB 변경은 롤백합니다",
    )
    root.add_argument("--offline-confirmed", action="store_true",
                      help="All database writers have been stopped")
    root.add_argument(
        "--replace",
        action="store_true",
        help="같은 ID가 DB에 있으면 JSON 내용으로 덮어씁니다 (주의)",
    )
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        counters = migrate(args)
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    prefix = "DRY-RUN · " if args.dry_run else ""
    print(prefix + f"database: {args.database}")
    labels = {
        "instructions": "instructions",
        "world_fact": "knowledge/world_fact",
        "interpretation": "knowledge/interpretation",
    }
    for key, label in labels.items():
        stats = counters[key]
        print(
            f"{label}: added={stats.get('added', 0)} "
            f"replaced={stats.get('replaced', 0)} skipped={stats.get('skipped', 0)}"
        )
    if args.dry_run:
        print("검증만 완료했습니다. SQLite 내용은 변경하지 않았습니다.")
    else:
        print("마이그레이션 완료. 기존 JSON 파일은 자동 삭제하지 않았습니다.")
        print("봇이 새 SQLite 데이터를 확인한 뒤 JSON 파일은 백업/삭제해도 됩니다.")


if __name__ == "__main__":
    main()
