"""Tests for the command discovery and help-only launcher."""

from __future__ import annotations

import sys
import tomllib
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from hina_bot.tooling import cli

ROOT = Path(__file__).resolve().parents[2]


def test_registered_commands_have_catalog_metadata():
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["scripts"]
    installed = cli.installed_commands()

    assert declared["hina"] == "hina_bot.tooling.cli:main"
    assert set(declared) == set(installed) | {"hina"}
    assert set(cli.COMMAND_INFO) == set(installed)
    assert all(info.category in cli.CATEGORY_ORDER for info in cli.COMMAND_INFO.values())


def test_root_shows_help_without_discovering_or_running_commands(monkeypatch, capsys):
    def do_not_discover():
        raise AssertionError("No command should be loaded when printing root help")

    monkeypatch.setattr(cli, "installed_commands", do_not_discover)
    assert cli.main([]) == 0
    output = capsys.readouterr().out
    assert "hina commands" in output
    assert "hina help" in output
    assert output.isascii()


def test_commands_grouped_and_warn_about_writes(capsys):
    assert cli.main(["commands"]) == 0
    output = capsys.readouterr().out
    assert "[Run]" in output
    assert "[Environment]" in output
    assert "[Database / Maintenance]" in output
    assert "[Evaluation / Analysis]" in output
    assert "hina-env-export" in output
    assert "hina-reset-conversation" in output
    assert " [CAUTION]" in output
    assert "hina help env-export" in output
    assert output.index("[Run]") < output.index("[Database / Maintenance]")
    assert output.isascii()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("env-export", "hina-env-export"),
        ("hina-env-export", "hina-env-export"),
        ("reset-conversation", "hina-reset-conversation"),
    ],
)
def test_command_aliases(value, expected):
    assert cli.command_name(value, cli.installed_commands()) == expected


def test_unsafe_command_help_calls_only_module_help(monkeypatch, capsys):
    calls = []

    def capture_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", capture_run)
    assert cli.main(["help", "reset-conversation"]) == 0
    assert calls == [
        (
            [sys.executable, "-m", "hina_bot.tooling.reset_conversation", "--help"],
            {"check": False},
        )
    ]
    output = capsys.readouterr().out
    assert "CAUTION" in output
    assert "Reset conversation history" in output
    assert output.isascii()


@pytest.mark.parametrize("name", ["bot", "dashboard"])
def test_service_help_never_starts_services(monkeypatch, capsys, name):
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *a, **k: pytest.fail("Must not execute a service to show help"),
    )
    assert cli.main(["help", name]) == 0
    output = capsys.readouterr().out
    assert "does not support --help" in output
    assert f"hina-{name}" in output
    assert output.isascii()


def test_unknown_help_does_not_execute_anything(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *a, **k: pytest.fail("Must not execute an unknown command"),
    )
    assert cli.main(["help", "unknown"]) == 2
    assert "Unknown command" in capsys.readouterr().err


def test_unclassified_new_entry_point_is_still_discoverable(capsys):
    extra = metadata.EntryPoint(
        name="hina-new-tool",
        value="hina_bot.tooling.some_tool:main",
        group="console_scripts",
    )
    cli.print_commands({"hina-new-tool": extra})
    output = capsys.readouterr().out
    assert "[Other]" in output
    assert "hina-new-tool" in output
    assert "No description" in output


def test_missing_install_metadata_has_actionable_error(monkeypatch, capsys):
    def missing():
        raise metadata.PackageNotFoundError(cli.DISTRIBUTION)

    monkeypatch.setattr(cli, "installed_commands", missing)
    assert cli.main(["commands"]) == 2
    assert "uv sync" in capsys.readouterr().err


def test_actual_env_export_help_is_safe_and_available(capfd):
    assert cli.main(["help", "env-export"]) == 0
    output = capfd.readouterr().out
    assert "hina-env-export" in output
    assert "--output" in output
    assert "--force" in output
