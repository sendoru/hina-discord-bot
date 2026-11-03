"""Export effective runtime settings plus startup-only .env values as a dotenv file."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from hina_bot.core.db_connections import open_readonly
from hina_bot.core.runtime_config import RUNTIME_SETTING_SPECS, decode_runtime_value

from .env_template import (
    atomic_write,
    ensure_distinct_output,
    parse_values,
    read_text,
    render_template,
)


def _runtime_to_env(attr: str, encoded: str) -> str | None:
    spec = RUNTIME_SETTING_SPECS[attr]
    try:
        value = decode_runtime_value(spec, encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid DB runtime setting {attr}: {exc}") from exc

    if spec.kind == "optional_bool":
        return None if value is None else str(value).lower()
    if spec.kind == "bool":
        return str(value).lower()
    if spec.kind == "prefixes":
        return ",".join(value)
    if spec.kind == "discord_ids":
        return ",".join(str(item) for item in sorted(value))
    return str(value)


def _read_runtime_db(db_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    db = open_readonly(db_path)
    try:
        # One consistent snapshot even if runtime settings are being changed.
        db.execute("BEGIN")
        startup = db.execute("SELECT key,value FROM runtime_config_startup").fetchall()
        overrides = db.execute("SELECT key,value FROM runtime_config").fetchall()
        db.commit()
    finally:
        db.close()

    def decode(rows: list[sqlite3.Row], table: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in rows:
            attr = str(row["key"])
            if attr not in RUNTIME_SETTING_SPECS:
                raise ValueError(f"Unknown runtime setting in {table}: {attr}")
            result[attr] = str(row["value"])
        return result

    return decode(startup, "runtime_config_startup"), decode(overrides, "runtime_config")


def export_env(
    env_path: Path, template_path: Path, db_path: Path
) -> tuple[str, set[str], int, int]:
    """Priority: DB override > startup snapshot > explicit .env > template."""
    existing = parse_values(read_text(env_path))
    template = read_text(template_path)
    startup, overrides = _read_runtime_db(db_path)
    values = existing.copy()
    disabled: set[str] = set()

    for attr, spec in RUNTIME_SETTING_SPECS.items():
        if attr in overrides:
            encoded = overrides[attr]
        elif attr in startup:
            encoded = startup[attr]
        else:
            continue
        value = _runtime_to_env(attr, encoded)
        if value is None:
            values.pop(spec.env_name, None)
            disabled.add(spec.env_name)
        else:
            values[spec.env_name] = value
            disabled.discard(spec.env_name)

    rendered, template_keys = render_template(
        template, values, disabled=frozenset(disabled)
    )
    missing = {spec.env_name for spec in RUNTIME_SETTING_SPECS.values()} - template_keys
    if missing:
        raise ValueError(f"Runtime settings missing from template: {sorted(missing)}")
    return rendered, set(existing) - template_keys, len(startup), len(overrides)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--template", type=Path, default=Path(".env.example"))
    parser.add_argument("--db", type=Path, help="Default: DATABASE_PATH from .env or template")
    parser.add_argument("--output", type=Path, default=Path(".env.exported"))
    parser.add_argument("--force", action="store_true", help="Replace an existing output file")
    args = parser.parse_args(argv)

    try:
        template = read_text(args.template)
        existing = parse_values(read_text(args.env))
        defaults = parse_values(template)
        db_path = args.db or Path(
            existing.get("DATABASE_PATH") or defaults.get("DATABASE_PATH") or "data/hina.sqlite3"
        )
        ensure_distinct_output(args.output, args.env, args.template, db_path)
        if args.output.exists() and not args.force:
            raise ValueError("Output already exists; use --force to overwrite it")

        rendered, dropped, startup_count, override_count = export_env(
            args.env, args.template, db_path
        )
        atomic_write(args.output, rendered)
        if dropped:
            print("Template does not include (omitted): " + ", ".join(sorted(dropped)))
        print(
            f"Exported {args.output} with {override_count} DB overrides and "
            f"{startup_count} startup snapshot entries."
        )
        print("Startup-only values come from --env; the DB snapshot is from the last bot start.")
        return 0
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
