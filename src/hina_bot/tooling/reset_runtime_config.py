"""Clear persisted runtime config overrides without changing startup or other data."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv

from hina_bot.core.admin_commands import enqueue_admin_command
from hina_bot.core.db_connections import open_existing, open_readonly
from hina_bot.core.db_schema import SchemaError, require_current
from hina_bot.core.settings_repository import SettingsRepository


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path.cwd() / ".env.local", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/hina.sqlite3"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Count overrides (default)")
    mode.add_argument("--yes", action="store_true", help="Confirm clearing all overrides")
    parser.add_argument(
        "--offline-confirmed", action="store_true",
        help="Delete directly; all bot/dashboard/other DB writers must be stopped",
    )
    args = parser.parse_args(argv)
    if args.offline_confirmed and not args.yes:
        parser.error("--offline-confirmed requires --yes")
    try:
        path = Path(args.database).expanduser().resolve(strict=True)
        with closing(open_readonly(path)) as db:
            require_current(db, ("store", "settings"))
            count = int(db.execute("SELECT COUNT(*) FROM runtime_config").fetchone()[0])
        print(f"Database: {path}\nStored overrides: {count}")
        if not args.yes:
            print("Dry run only. Pass --yes to enqueue the reset.")
            return 0
        if args.offline_confirmed:
            with closing(open_existing(path)) as db:
                require_current(db, ("store", "settings"))
                if db.execute(
                    "SELECT 1 FROM admin_commands WHERE action LIKE 'runtime.%' "
                    "AND status IN ('pending', 'running') LIMIT 1"
                ).fetchone():
                    raise ValueError("Pending runtime commands; let the bot finish them first")
                cleared = SettingsRepository(db).clear_runtime_values()
            print(f"Cleared {cleared} runtime overrides (offline).")
        else:
            command_id, _ = enqueue_admin_command(
                path, request_id=uuid.uuid4().hex, action="runtime.reset_all",
                target="all", actor="local-cli",
            )
            print(f"Queued command #{command_id}; the bot will apply it when it runs.")
            print("This does not confirm completion; check the runtime admin command status.")
        return 0
    except (OSError, sqlite3.Error, SchemaError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
