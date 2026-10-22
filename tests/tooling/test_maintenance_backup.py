import sqlite3

import pytest

from hina_bot.core.store import Store
from hina_bot.tooling import maintenance_backup


@pytest.mark.parametrize("verification_fails", [False, True])
def test_backup_closes_all_connections_before_publish_or_cleanup(
    tmp_path, monkeypatch, verification_fails,
):
    path = tmp_path / "source.sqlite3"
    Store.create(str(path)).close()
    connections = []
    connect = sqlite3.connect
    counts = maintenance_backup._counts
    calls = 0

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            super().close()
            self.closed = True

    def track_connect(*args, **kwargs):
        db = connect(*args, **kwargs, factory=TrackedConnection)
        connections.append(db)
        return db

    def verify_counts(db):
        nonlocal calls
        calls += 1
        if verification_fails and calls == 2:
            raise RuntimeError("verification failure")
        return counts(db)

    # On Windows an open verification connection can prevent unlinking the snapshot.
    unlink = type(path).unlink
    link = maintenance_backup.os.link

    def check_unlink(self, *args, **kwargs):
        if self.suffix == ".partial":
            assert all(db.closed for db in connections[1:])
        return unlink(self, *args, **kwargs)

    def check_link(*args, **kwargs):
        assert all(db.closed for db in connections[1:])
        return link(*args, **kwargs)

    monkeypatch.setattr(maintenance_backup.sqlite3, "connect", track_connect)
    monkeypatch.setattr(maintenance_backup, "_counts", verify_counts)
    monkeypatch.setattr(type(path), "unlink", check_unlink)
    monkeypatch.setattr(maintenance_backup.os, "link", check_link)
    if verification_fails:
        with pytest.raises(RuntimeError, match="verification failure"):
            maintenance_backup.backup_sqlite(path)
        assert not list((tmp_path / "backups").glob("*.sqlite3"))
        assert not list((tmp_path / "backups").glob("*.partial"))
    else:
        result = maintenance_backup.backup_sqlite(path)
        assert result.path.is_file()
    assert len(connections) == 3
    assert all(db.closed for db in connections)
