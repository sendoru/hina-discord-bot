"""Read-only, content-free raw retention/backlog report. Never cleans up or calls an LLM."""

import argparse
import json
import os
from pathlib import Path

from hina_bot.core.db_schema import require_current
from hina_bot.core.settings_repository import SettingsRepository

from .maintenance_db import connect_existing


def retention_report(database, *, raw_retention_turns=1000):
    if not 1 <= raw_retention_turns <= 100000:
        raise ValueError("raw_retention_turns must be between 1 and 100000")
    path = Path(database)
    db = connect_existing(path, readonly=True)
    try:
        db.execute("BEGIN")
        require_current(db, ("store", "settings"))
        modes = SettingsRepository(db).memory_mode_overrides()
        result = {"raw_retention_turns": raw_retention_turns, "tables": {}}
        for table, cursor_table in (
            ("turns", "memory_extraction_cursors"),
            ("shared_calls", "shared_summaries"),
            ("failed_turns", None),
        ):
            pending = "t.id>COALESCE(c.through_id,0)" if cursor_table else "0"
            join = f"LEFT JOIN {cursor_table} c ON c.scope=t.scope" if cursor_table else ""
            # Stream one aggregate row per scope, never raw text or an unbounded fetchall.
            rows = db.execute(
                f"SELECT t.scope,t.realm,t.user_id,COUNT(*) AS total,"
                f"SUM({pending}) AS pending,"
                f"MAX(CASE WHEN {pending} THEN "
                "MAX(0,CAST((julianday('now')-julianday(t.created_at))*86400 AS INTEGER)) "
                f"ELSE NULL END) AS oldest_pending_seconds FROM {table} t {join} "
                "GROUP BY t.scope,t.realm,t.user_id"
            )
            stats = {"rows": 0, "scopes": 0, "pending": 0, "pending_write_disabled": 0,
                     "max_per_scope": 0, "over_target_rows": 0, "oldest_pending_seconds": None}
            for row in rows:
                total, count = int(row["total"]), int(row["pending"])
                stats["rows"] += total
                stats["scopes"] += 1
                stats["pending"] += count
                stats["max_per_scope"] = max(stats["max_per_scope"], total)
                stats["over_target_rows"] += max(0, total - raw_retention_turns)
                age = row["oldest_pending_seconds"]
                if age is not None:
                    stats["oldest_pending_seconds"] = max(
                        stats["oldest_pending_seconds"] or 0, int(age),
                    )
                channel = row["scope"].rsplit(":user:", 1)[0]
                admin = modes.get("global", "normal")
                if row["realm"].startswith("guild:"):
                    admin = modes.get(row["realm"], admin)
                admin = modes.get(channel, admin)
                owner = modes.get(f"user:{row['user_id']}", "normal")
                if admin in {"off", "read_only"} or owner in {"off", "read_only"}:
                    stats["pending_write_disabled"] += count
            result["tables"][table] = stats
        result["storage"] = {
            "database_bytes": path.stat().st_size,
            "wal_bytes": Path(str(path) + "-wal").stat().st_size
            if Path(str(path) + "-wal").exists() else 0,
            "page_count": db.execute("PRAGMA page_count").fetchone()[0],
            "freelist_count": db.execute("PRAGMA freelist_count").fetchone()[0],
        }
        return result
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/hina.sqlite3"))
    parser.add_argument("--raw-retention-turns", type=int,
                        default=int(os.getenv("RAW_RETENTION_TURNS", "1000")))
    args = parser.parse_args(argv)
    print(json.dumps(retention_report(
        args.database, raw_retention_turns=args.raw_retention_turns,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
