"""Discover Hina CLI commands and display their help without running operations."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata

DISTRIBUTION = "hina-discord-bot"
CATEGORY_ORDER = (
    "Run",
    "Environment",
    "Database / Maintenance",
    "Lore / Data",
    "Evaluation / Analysis",
)


@dataclass(frozen=True)
class CommandInfo:
    category: str
    description: str
    native_help: bool = True
    warning: bool = False


# Descriptions/classifications only: the commands themselves come from installed entry points.
COMMAND_INFO: dict[str, CommandInfo] = {
    "hina-bot": CommandInfo("Run", "Start the Discord bot", native_help=False),
    "hina-dashboard": CommandInfo("Run", "Start the web dashboard", native_help=False),
    "hina-env-normalize": CommandInfo(
        "Environment", "Normalize and back up a dotenv file", warning=True
    ),
    "hina-env-export": CommandInfo(
        "Environment", "Export runtime settings to a dotenv file", warning=True
    ),
    "hina-prepare-db": CommandInfo(
        "Database / Maintenance", "Inspect or prepare the database schema", warning=True
    ),
    "hina-flush-memory": CommandInfo(
        "Database / Maintenance", "Process pending memory extraction", warning=True
    ),
    "hina-backfill-annotations": CommandInfo(
        "Database / Maintenance", "Backfill memory annotations", warning=True
    ),
    "hina-reset-conversation": CommandInfo(
        "Database / Maintenance", "Reset conversation history", warning=True
    ),
    "hina-reset-analysis-data": CommandInfo(
        "Database / Maintenance", "Reset analysis data and logs", warning=True
    ),
    "hina-lore": CommandInfo("Lore / Data", "Validate and manage lore data"),
    "hina-eval": CommandInfo("Evaluation / Analysis", "Run conversation evaluations"),
    "hina-routing-eval": CommandInfo("Evaluation / Analysis", "Evaluate model routing"),
    "hina-memory-report": CommandInfo(
        "Evaluation / Analysis", "Generate a long-term memory report"
    ),
    "hina-retention-report": CommandInfo(
        "Database / Maintenance", "Inspect raw retention and protected backlog (read-only)"
    ),
    "hina-memory-extraction-eval": CommandInfo(
        "Evaluation / Analysis", "Evaluate memory extraction"
    ),
    "hina-memory-response-eval": CommandInfo(
        "Evaluation / Analysis", "Evaluate memory-assisted responses"
    ),
}


def installed_commands() -> dict[str, metadata.EntryPoint]:
    """Read this distribution's console scripts, not other installed applications."""
    distribution = metadata.distribution(DISTRIBUTION)
    return {
        point.name: point
        for point in distribution.entry_points
        if point.group == "console_scripts" and point.name.startswith("hina-")
    }


def command_name(value: str, commands: Mapping[str, metadata.EntryPoint]) -> str | None:
    candidate = value if value.startswith("hina-") else f"hina-{value}"
    return candidate if candidate in commands else None


def print_commands(commands: Mapping[str, metadata.EntryPoint]) -> None:
    print(f"Hina Discord Bot CLI - {len(commands)} commands\n")
    for category in (*CATEGORY_ORDER, "Other"):
        group = [
            name for name in commands
            if COMMAND_INFO.get(name, CommandInfo("Other", "No description")).category == category
        ]
        if not group:
            continue
        print(f"[{category}]")
        for name in sorted(group):
            info = COMMAND_INFO.get(name, CommandInfo("Other", "No description"))
            caution = " [CAUTION]" if info.warning else ""
            print(f"  {name:<30} {info.description}{caution}")
        print()
    print("Command help: hina help <command>")
    print("Example: hina help env-export")
    print("[CAUTION] Running these commands may modify files or the database.")


def show_command_help(
    value: str, commands: Mapping[str, metadata.EntryPoint]
) -> int:
    name = command_name(value, commands)
    if name is None:
        print(
            f"Unknown command: {value}. Run 'hina commands' to see available commands.",
            file=sys.stderr,
        )
        return 2

    info = COMMAND_INFO.get(name)
    if info is not None:
        caution = "\nCAUTION: Running this command may modify files or the database." if info.warning else ""
        print(f"{name} - {info.description}{caution}", flush=True)
        if not info.native_help:
            print("This service command does not support --help.")
            print(f"Run: {name}")
            return 0
    else:
        print(f"{name} - No description", flush=True)

    # Invoke only the documented --help path, in a separate process. Never load and
    # call a command's main() directly: bot/dashboard would actually start services.
    point = commands[name]
    try:
        return subprocess.run(
            [sys.executable, "-m", point.module, "--help"],
            check=False,
        ).returncode
    except OSError as exc:
        print(f"Failed to show help: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hina",
        description="Browse Hina Discord Bot commands and help",
    )
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("commands", help="List installed Hina commands by category")
    help_parser = subparsers.add_parser("help", help="Show help for an installed command")
    help_parser.add_argument("command", help="e.g. env-export or hina-env-export")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.action is None:
        parser.print_help()
        print("\nAvailable commands: hina commands")
        print("Command help: hina help <command>")
        return 0

    try:
        commands = installed_commands()
    except metadata.PackageNotFoundError:
        print(
            "Hina package metadata not found. Run 'uv sync' or "
            "'pip install -e .' to install the package.",
            file=sys.stderr,
        )
        return 2

    if args.action == "commands":
        print_commands(commands)
        return 0
    if args.action == "help":
        return show_command_help(args.command, commands)
    parser.error(f"Unknown action: {args.action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
