"""Normalize a local .env to .env.example ordering and comments, keeping values."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .env_template import (
    atomic_write,
    backup_env,
    parse_values,
    read_text,
    render_template,
)


def normalize_env(env_path: Path, template_path: Path) -> tuple[str, set[str]]:
    existing = parse_values(read_text(env_path))
    rendered, template_keys = render_template(read_text(template_path), existing)
    return rendered, set(existing) - template_keys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--template", type=Path, default=Path(".env.example"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Default: preview changes without writing")
    mode.add_argument("--write", action="store_true", help="Back up and replace the .env file")
    args = parser.parse_args(argv)

    try:
        if args.write and args.env.is_symlink():
            raise ValueError("Refusing to replace a symlinked .env")
        rendered, removed = normalize_env(args.env, args.template)
        current = read_text(args.env)
        if removed:
            print("Template does not include (will omit): " + ", ".join(sorted(removed)))
        if rendered == current:
            print("Already matches the template; no changes.")
            return 0
        if not args.write:
            print("Changes needed; run with --write to back up and normalize the file.")
            return 1
        backup = backup_env(args.env)
        atomic_write(args.env, rendered)
        print(f"Normalized {args.env}; backup: {backup}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
