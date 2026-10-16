"""Isolated admin data for evaluations; an optional source is read-only."""

import sqlite3

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.db_connections import open_readonly
from hina_bot.core.db_schema import prepare_connection


def evaluation_admin_database(source=None):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        if source:
            original = open_readonly(source)
            try:
                original.backup(db)
            finally:
                original.close()
        prepare_connection(db, ("admin",))
        return AdminDatabase(db)
    except BaseException:
        db.close()
        raise
