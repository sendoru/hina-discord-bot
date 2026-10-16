"""Inspect without writes, or explicitly back up and prepare SQLite schemas."""

import argparse
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from hina_bot.core.db_connections import open_existing, open_readonly
from hina_bot.core.db_schema import (
    COMPONENTS,
    database_signature,
    inspect_connection,
    prepare_connection,
)

from .maintenance_backup import backup_schema_database


def prepare_database(
    path, *, apply=False, components=COMPONENTS, backup_dir=None, offline_confirmed=False
):
    path = Path(path).expanduser()
    signature = None
    if path.exists():
        db = open_readonly(path)
        try:
            db.execute("BEGIN")
            pending = inspect_connection(db, components)
            if apply and pending:
                signature = database_signature(db)
        finally:
            db.close()
    else:
        db = sqlite3.connect(":memory:")
        try:
            pending = inspect_connection(db, components)
        finally:
            db.close()
    if not apply or not pending:
        return pending, None
    if not offline_confirmed:
        raise ValueError("Stop all database writers and pass --offline-confirmed")
    backup = backup_schema_database(path, backup_dir=backup_dir) if path.exists() else None
    if signature is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
    db = open_existing(path)
    try:
        changed = prepare_connection(db, components, expected_signature=signature)
        # This step is deliberately after the migration commit; a PRAGMA failure
        # must not be described as a rollback of already committed schema changes.
        try:
            mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                raise RuntimeError("Schema committed, but WAL configuration was not applied")
        except sqlite3.Error as exc:
            raise RuntimeError("Schema committed, but WAL configuration failed") from exc
        return changed, backup
    finally:
        db.close()


def main(argv=None):
    load_dotenv(Path.cwd() / ".env.local", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/hina.sqlite3"))
    parser.add_argument("--component", action="append", choices=COMPONENTS)
    parser.add_argument("--backup-dir")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--yes", action="store_true")
    parser.add_argument("--offline-confirmed", action="store_true")
    args = parser.parse_args(argv)
    print(f'Database: {Path(args.database).expanduser().resolve()}')
    try:
        pending, backup = prepare_database(
            args.database,
            apply=args.yes,
            components=args.component or COMPONENTS,
            backup_dir=args.backup_dir,
            offline_confirmed=args.offline_confirmed,
        )
        print("Schema areas: " + (", ".join(pending) or "already current"))
        if backup:
            print(f"Backup: {backup.path}; SHA256={backup.sha256}")
        if not args.yes:
            print("Dry-run only; no target database changes.")
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
