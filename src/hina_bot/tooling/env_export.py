"""Export effective runtime settings as dotenv and a provider preferences JSON file."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from hina_bot.core.config import load_openrouter_preferences, migrate_legacy_env_values
from hina_bot.core.db_connections import open_readonly
from hina_bot.core.runtime_config import (
    OPENROUTER_PREFERENCES_FILE_SNAPSHOT_KEY,
    RUNTIME_SETTING_SPECS,
    decode_runtime_value,
)

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
            if attr == "memory_session_followup":
                continue
            if (
                table == "runtime_config_startup"
                and attr == OPENROUTER_PREFERENCES_FILE_SNAPSHOT_KEY
            ):
                continue
            if attr not in RUNTIME_SETTING_SPECS:
                raise ValueError(f"Unknown runtime setting in {table}: {attr}")
            result[attr] = str(row["value"])
        return result

    return decode(startup, "runtime_config_startup"), decode(overrides, "runtime_config")


def export_env(
    env_path: Path, template_path: Path, db_path: Path, *, preferences_output: Path
) -> tuple[str, str, set[str], int, int]:
    """Priority: DB override > startup snapshot > explicit .env > template."""
    existing = parse_values(read_text(env_path))
    template = read_text(template_path)
    startup, overrides = _read_runtime_db(db_path)
    values = migrate_legacy_env_values(existing)
    disabled: set[str] = set()
    preferences_key = "openrouter_provider_preferences"
    preferences_env = "OPENROUTER_PROVIDER_PREFERENCES_FILE"
    encoded_preferences = overrides.get(preferences_key, startup.get(preferences_key))
    if encoded_preferences is None:
        source = values.get(preferences_env, parse_values(template).get(preferences_env, ""))
        preferences = load_openrouter_preferences(source.strip())
    else:
        try:
            preferences = decode_runtime_value(
                RUNTIME_SETTING_SPECS[preferences_key], encoded_preferences
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid DB runtime setting {preferences_key}: {exc}") from exc
    preferences_json = json.dumps(preferences, ensure_ascii=False, indent=2) + "\n"
    values[preferences_env] = str(preferences_output.expanduser())
    values.pop("OPENROUTER_PROVIDER_PREFERENCES", None)

    for attr, spec in RUNTIME_SETTING_SPECS.items():
        if attr == preferences_key:
            continue
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
    expected_keys = {
        spec.env_name for spec in RUNTIME_SETTING_SPECS.values()
        if spec.attr != preferences_key
    } | {preferences_env}
    missing = expected_keys - template_keys
    if missing:
        raise ValueError(f"Runtime settings missing from template: {sorted(missing)}")
    if "OPENROUTER_PROVIDER_PREFERENCES" in template_keys:
        raise ValueError("Use a current template without inline provider preferences")
    return rendered, preferences_json, set(existing) - template_keys, len(startup), len(overrides)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--template", type=Path, default=Path(".env.example"))
    parser.add_argument("--db", type=Path, help="Default: DATABASE_PATH from .env or template")
    parser.add_argument("--output", type=Path, default=Path(".env.exported"))
    parser.add_argument("--force", action="store_true", help="Replace existing output files")
    args = parser.parse_args(argv)

    try:
        template = read_text(args.template)
        existing = parse_values(read_text(args.env))
        defaults = parse_values(template)
        db_path = args.db or Path(
            existing.get("DATABASE_PATH") or defaults.get("DATABASE_PATH") or "data/hina.sqlite3"
        )
        preferences_output = args.output.with_name(
            f"{args.output.name}.openrouter-provider-preferences.json"
        )
        protected = [args.env, args.template, db_path]
        source = existing.get(
            "OPENROUTER_PROVIDER_PREFERENCES_FILE",
            defaults.get("OPENROUTER_PROVIDER_PREFERENCES_FILE", ""),
        ).strip()
        if source:
            protected.append(Path(source))
        ensure_distinct_output(args.output, *protected, preferences_output)
        ensure_distinct_output(preferences_output, *protected, args.output)
        for output in (args.output, preferences_output):
            if output.expanduser().exists() and not args.force:
                raise ValueError(f"Output {output} already exists; use --force to overwrite it")

        rendered, preferences_json, dropped, startup_count, override_count = export_env(
            args.env, args.template, db_path, preferences_output=preferences_output,
        )
        atomic_write(preferences_output, preferences_json)
        atomic_write(args.output, rendered)
        print(f"Exported provider preferences to {preferences_output}.")
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
