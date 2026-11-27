import sqlite3

from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.tooling.reset_runtime_config import main


def _database(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store.create(str(path))
    try:
        runtime = RuntimeSettings(Settings(discord_token="test"), store)
        runtime.capture_startup_snapshot()
        runtime.set_text("channel_context_chars", "4300")
        store.settings.set_note("unrelated", "keep")
    finally:
        store.close()
    return path


def test_cli_dry_run_and_live_queue(tmp_path, capsys):
    path = _database(tmp_path)
    args = ["--database", str(path)]
    assert main(args) == 0
    assert "Stored overrides: 1" in capsys.readouterr().out
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM admin_commands").fetchone()[0] == 0
    assert main([*args, "--yes"]) == 0
    assert "Queued command" in capsys.readouterr().out
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT actor,action,status FROM admin_commands").fetchone() == (
            "local-cli", "runtime.reset_all", "pending",
        )
        assert db.execute("SELECT COUNT(*) FROM runtime_config").fetchone()[0] == 1
    assert main([*args, "--yes", "--offline-confirmed"]) == 2


def test_cli_offline_reset_preserves_startup_and_other_data(tmp_path):
    path = _database(tmp_path)
    args = ["--database", str(path), "--yes", "--offline-confirmed"]
    assert main(args) == 0
    assert main(args) == 0
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_config").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM runtime_config_startup").fetchone()[0]
        assert db.execute("SELECT text FROM notes WHERE scope='unrelated'").fetchone() == ("keep",)


def test_cli_does_not_create_missing_database(tmp_path):
    path = tmp_path / "missing.sqlite3"
    assert main(["--database", str(path), "--yes"]) == 2
    assert not path.exists()
