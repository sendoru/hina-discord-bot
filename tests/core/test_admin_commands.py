import sqlite3

from hina_bot.core.admin_commands import (
    claim_next_admin_command,
    enqueue_admin_command,
    finish_admin_command,
    mark_interrupted_admin_commands,
)
from hina_bot.core.store import Store


def test_admin_command_queue_is_idempotent_and_scrubs_payload(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store(str(path))
    store.close()

    request_id = "a" * 32
    first, created = enqueue_admin_command(
        path,
        request_id=request_id,
        action="runtime.set",
        target="model",
        payload={"value": "secret-ish"},
    )
    second, duplicate_created = enqueue_admin_command(
        path,
        request_id=request_id,
        action="runtime.set",
        target="model",
        payload={"value": "different"},
    )
    assert first == second
    assert created is True
    assert duplicate_created is False

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    command = claim_next_admin_command(db)
    assert command is not None
    assert command.action == "runtime.set"
    assert command.payload == {"value": "secret-ish"}

    finish_admin_command(db, command.id, result={"applied": True})
    row = db.execute(
        "SELECT status,payload_json,result_json FROM admin_commands WHERE id=?",
        (command.id,),
    ).fetchone()
    assert row["status"] == "succeeded"
    assert row["payload_json"] == "{}"
    assert row["result_json"] == '{"applied":true}'
    db.close()


def test_interrupted_admin_commands_are_failed_without_retry(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store(str(path))
    store.close()

    enqueue_admin_command(
        path,
        request_id="b" * 32,
        action="memory.mode",
        target="global",
        payload={"mode": "off"},
    )

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    command = claim_next_admin_command(db)
    assert command is not None
    assert mark_interrupted_admin_commands(db) == 1
    row = db.execute(
        "SELECT status,payload_json,error_type FROM admin_commands WHERE id=?",
        (command.id,),
    ).fetchone()
    assert row["status"] == "failed"
    assert row["payload_json"] == "{}"
    assert row["error_type"] == "InterruptedAdminCommand"
    assert claim_next_admin_command(db) is None
    db.close()
