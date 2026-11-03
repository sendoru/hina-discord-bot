"""Regression tests for dotenv normalization and effective runtime export."""

from __future__ import annotations

from pathlib import Path

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.tooling.env_export import export_env, main as export_main
from hina_bot.tooling.env_normalize import main as normalize_main, normalize_env
from hina_bot.tooling.env_template import parse_template, parse_values, render_template

ROOT = Path(__file__).resolve().parents[2]


def test_normalize_preserves_template_comments_order_and_explicit_empty(tmp_path):
    template = tmp_path / ".env.example"
    source = tmp_path / ".env"
    template.write_text(
        "# Heading\nA=default\n# OPTIONAL=false\nB=default # note\n\n",
        encoding="utf-8",
    )
    source.write_text(
        "B=special\nOPTIONAL=false\nA=\nEXTRA=unused\n", encoding="utf-8"
    )
    rendered, dropped = normalize_env(source, template)
    assert rendered == "# Heading\nA=\nOPTIONAL=false\nB=special # note\n\n"
    assert dropped == {"EXTRA"}


def test_renderer_keeps_optional_comment_and_template_defaults():
    template = "A=default\n# OPTIONAL=false\nB=two\n"
    rendered, _ = render_template(template, {"A": "set"})
    assert rendered == "A=set\n# OPTIONAL=false\nB=two\n"
    rendered, _ = render_template(template, {"A": "set", "OPTIONAL": ""})
    assert rendered == "A=set\nOPTIONAL=\nB=two\n"
    rendered, _ = render_template(template, {"A": "other"}, disabled=frozenset({"A"}))
    assert rendered == "# A=default\n# OPTIONAL=false\nB=two\n"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  leading and trailing  ",
        "text # literal hash",
        "mixed ' \" quotes",
        "C:\\temp\\file",
        "two\nlines",
        "some $VARIABLE literal",
        "한글과 공백",
    ],
)
def test_value_roundtrip_without_interpolation(value):
    rendered, _ = render_template("KEY=default\n", {"KEY": value})
    assert parse_values(rendered) == {"KEY": value}


def test_template_preserves_inline_comment_outside_quoted_hash():
    template = 'A="hello #inside" # trailing note\n'
    rendered, _ = render_template(template, {"A": "changed"})
    assert rendered == "A=changed # trailing note\n"
    assert parse_values(rendered)["A"] == "changed"


def test_template_and_dotenv_duplicate_or_invalid_rejected():
    with pytest.raises(ValueError, match="Duplicate template"):
        parse_template("KEY=1\n# KEY=2\n")
    with pytest.raises(ValueError, match="Duplicate dotenv"):
        parse_values("KEY=1\nKEY=2\n")
    with pytest.raises(ValueError, match="Missing '='"):
        parse_values("KEY\n")
    with pytest.raises(ValueError, match="Unsupported"):
        parse_template("export KEY=1\n")


def test_normalize_check_write_backup_and_idempotence(tmp_path, capsys):
    source = tmp_path / ".env"
    template = tmp_path / ".env.example"
    template.write_text("A=default\nB=two\n", encoding="utf-8")
    source.write_text("B=custom\nA=secret\n", encoding="utf-8")
    argv = ["--env", str(source), "--template", str(template)]
    assert normalize_main([*argv, "--check"]) == 1
    assert source.read_text() == "B=custom\nA=secret\n"
    assert normalize_main([*argv, "--write"]) == 0
    assert source.read_text() == "A=secret\nB=custom\n"
    backups = list(tmp_path.glob(".env.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "B=custom\nA=secret\n"
    assert source.stat().st_mode & 0o777 == 0o600
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert normalize_main([*argv, "--check"]) == 0
    assert normalize_main([*argv, "--write"]) == 0
    assert len(list(tmp_path.glob(".env.bak.*"))) == 1
    assert "secret" not in capsys.readouterr().out


def test_normalize_missing_input_does_not_create_env(tmp_path):
    source = tmp_path / ".env"
    template = tmp_path / ".env.example"
    template.write_text("A=default\n", encoding="utf-8")
    assert normalize_main(["--env", str(source), "--template", str(template), "--write"]) == 2
    assert not source.exists()


def test_export_db_precedence_and_startup_only_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "DISCORD_TOKEN=secret-token\n"
        "OPENAI_API_KEY=secret-api-key\n"
        "LLM_MODEL=env-model\n"
        "OUTPUT_REPAIR_MODE=off\n"
        "OUTPUT_REPAIR_TIMEOUT_SECONDS=5\n"
        "GEMINI_STORE_ANSWER_INTERACTIONS=false\n"
        "DATABASE_PATH=other-db.sqlite3\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "actual.sqlite3"
    store = Store.create(str(db_path))
    try:
        settings = RuntimeSettings(
            Settings(
                discord_token="token",
                openai_api_key="key",
                model="startup-model",
                output_repair_timeout_seconds=2.5,
                gemini_store_answer_interactions=None,
            ),
            store,
        )
        settings.capture_startup_snapshot()
        settings.set_text("LLM_MODEL", "db-model")
        settings.set_text("OUTPUT_REPAIR_MODE", "active")
    finally:
        store.close()

    rendered, dropped, count, override_count = export_env(env, ROOT / ".env.example", db_path)
    exported = parse_values(rendered)
    assert exported["DISCORD_TOKEN"] == "secret-token"
    assert exported["OPENAI_API_KEY"] == "secret-api-key"
    assert exported["DATABASE_PATH"] == "other-db.sqlite3"
    assert exported["LLM_MODEL"] == "db-model"
    assert exported["OUTPUT_REPAIR_MODE"] == "active"
    assert exported["OUTPUT_REPAIR_TIMEOUT_SECONDS"] == "2.5"
    assert "GEMINI_STORE_ANSWER_INTERACTIONS" not in exported
    assert "# GEMINI_STORE_ANSWER_INTERACTIONS=false" in rendered
    assert not dropped
    assert count > 0
    assert override_count == 2

    output = tmp_path / ".env.exported"
    argv = [
        "--env", str(env), "--template", str(ROOT / ".env.example"),
        "--db", str(db_path), "--output", str(output),
    ]
    assert export_main(argv) == 0
    assert output.stat().st_mode & 0o777 == 0o600
    assert export_main(argv) == 2  # No overwriting without --force.
    assert export_main([*argv, "--force"]) == 0
    assert export_main([*argv, "--output", str(env), "--force"]) == 2


def test_export_db_optional_bool_true_false_and_inherit(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "GEMINI_STORE_MEMORY_INTERACTIONS=true\nDATABASE_PATH=d.sqlite3\n",
        encoding="utf-8",
    )
    # Use the repository template because runtime parity is required for full export.
    template = ROOT / ".env.example"
    store = Store.create(str(tmp_path / "d.sqlite3"))
    try:
        settings = RuntimeSettings(Settings(discord_token="token"), store)
        settings.capture_startup_snapshot()
        settings.set_text("GEMINI_STORE_MEMORY_INTERACTIONS", "off")
        rendered, _, _, _ = export_env(env, template, tmp_path / "d.sqlite3")
        assert parse_values(rendered)["GEMINI_STORE_MEMORY_INTERACTIONS"] == "false"
        settings.set_text("GEMINI_STORE_MEMORY_INTERACTIONS", "inherit")
        rendered, _, _, _ = export_env(env, template, tmp_path / "d.sqlite3")
        assert "GEMINI_STORE_MEMORY_INTERACTIONS" not in parse_values(rendered)
    finally:
        store.close()


def test_export_invalid_db_value_fails_without_writing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=secret\nDATABASE_PATH=db.sqlite3\n", encoding="utf-8")
    db = tmp_path / "db.sqlite3"
    store = Store.create(str(db))
    try:
        store.settings.set_runtime_value("model", "{invalid")
    finally:
        store.close()
    out = tmp_path / "export"
    rc = export_main(
        ["--env", str(env), "--template", str(ROOT / ".env.example"),
         "--db", str(db), "--output", str(out)]
    )
    assert rc == 2
    assert not out.exists()


def test_export_protects_input_paths_and_missing_db(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DATABASE_PATH=missing.sqlite3\n", encoding="utf-8")
    template = ROOT / ".env.example"
    assert export_main([
        "--env", str(env), "--template", str(template),
        "--output", str(env), "--force",
    ]) == 2
    assert env.read_text() == "DATABASE_PATH=missing.sqlite3\n"
    assert export_main([
        "--env", str(env), "--template", str(template),
        "--output", str(tmp_path / "not-created"),
    ]) == 2
    assert not (tmp_path / "not-created").exists()
