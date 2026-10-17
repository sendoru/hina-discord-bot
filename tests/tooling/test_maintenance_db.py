"""Failed maintenance opens must not leak the connection they acquired."""

import sqlite3

import pytest

from hina_bot.tooling import maintenance_db


@pytest.mark.parametrize("readonly,failure_prefix", [
    (False, "PRAGMA busy_timeout"),
    (False, "PRAGMA secure_delete"),
    (False, "SELECT 1 FROM sqlite_master"),
    (True, "PRAGMA busy_timeout"),
    (True, "SELECT 1 FROM sqlite_master"),
])
def test_failed_connection_setup_closes_the_owned_connection(
    tmp_path, monkeypatch, readonly, failure_prefix,
):
    class FailingConnection(sqlite3.Connection):
        reject_prefix = ""

        def execute(self, sql, *args):
            if self.reject_prefix and sql.startswith(self.reject_prefix):
                raise sqlite3.OperationalError("injected setup failure")
            return super().execute(sql, *args)

    path = tmp_path / "maintenance.sqlite3"
    db = sqlite3.connect(path, factory=FailingConnection)
    db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY)")
    db.commit()
    db.reject_prefix = failure_prefix
    opener = "open_readonly" if readonly else "open_existing"
    monkeypatch.setattr(maintenance_db, opener, lambda path: db)

    try:
        with pytest.raises(sqlite3.OperationalError, match="injected setup failure"):
            maintenance_db.connect_existing(path, readonly=readonly)
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.execute("SELECT 1")
    finally:
        db.close()
