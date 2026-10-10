import json
import sqlite3

from fastapi.testclient import TestClient

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings


def _settings(tmp_path, database, *, write_enabled=True):
    return DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=write_enabled,
    )


def _memory_database(path):
    store = Store(str(path))
    scope = Scope(1, 10, 100, True)
    item_id = store.add_memory_item(
        scope,
        "likes coffee",
        kind="preference",
        disclosure="implicit",
        source_message_ids=("55",),
        confidence=0.8,
        user_name="Dashboard User",
    )
    store.close()
    return item_id


def test_memory_detail_editor_enqueues_only_editable_fields(tmp_path):
    database = tmp_path / "hina.sqlite3"
    item_id = _memory_database(database)
    app = create_app(_settings(tmp_path, database, write_enabled=True))

    with TestClient(app) as client:
        detail = client.get(f"/memory/{item_id}")
        assert detail.status_code == 200
        assert 'action="/memory/1/edit"' in detail.text
        assert 'name="expected_revision" value="0"' in detail.text
        assert "memory-editor.js" in detail.text

        response = client.post(
            f"/memory/{item_id}/edit",
            data={
                "_csrf": app.state.admin_writer.csrf_token,
                "_request_id": "a" * 32,
                "expected_revision": "0",
                "content": "likes espresso",
                "kind": "preference",
                "disclosure": "global",
                "confidence": "0.95",
                "relationship_evidence_familiarity": "4",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith(f"/memory/{item_id}?queued=")

    db = sqlite3.connect(database)
    try:
        row = db.execute(
            """SELECT action,target,payload_json,status
               FROM admin_commands ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    finally:
        db.close()
    assert row[0] == "memory.item.edit"
    assert row[1] == str(item_id)
    assert row[3] == "pending"
    payload = json.loads(row[2])
    assert payload == {
        "item_id": item_id,
        "expected_revision": 0,
        "content": "likes espresso",
        "kind": "preference",
        "disclosure": "global",
        "confidence": 0.95,
        "relationship_evidence": {},
    }
    assert "user_id" not in payload
    assert "origin_realm" not in payload
    assert "origin_channel_id" not in payload


def test_memory_detail_renders_revision_history(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store(str(database))
    scope = Scope(None, 10, 100)
    item_id = store.add_memory_item(
        scope,
        "before",
        kind="fact",
        disclosure="local",
        confidence=0.7,
    )
    store.revise_memory_item(
        item_id,
        expected_revision=0,
        content="after",
        kind="fact",
        disclosure="global",
        confidence=0.9,
        admin_command_id=12,
    )
    store.close()

    app = create_app(_settings(tmp_path, database, write_enabled=True))
    with TestClient(app) as client:
        response = client.get(f"/memory/{item_id}")

    assert response.status_code == 200
    assert "Revision 0 → 1" in response.text
    assert "content, disclosure, confidence changed" in response.text
    assert "before" in response.text
    assert "#12" in response.text
    assert 'name="expected_revision" value="1"' in response.text


def test_memory_edit_requires_write_mode_and_csrf(tmp_path):
    database = tmp_path / "hina.sqlite3"
    item_id = _memory_database(database)

    read_only_app = create_app(_settings(tmp_path, database, write_enabled=False))
    with TestClient(read_only_app) as client:
        response = client.post(
            f"/memory/{item_id}/edit",
            data={"_csrf": read_only_app.state.admin_writer.csrf_token},
        )
    assert response.status_code == 403

    writable_app = create_app(_settings(tmp_path, database, write_enabled=True))
    with TestClient(writable_app) as client:
        response = client.post(
            f"/memory/{item_id}/edit",
            data={
                "_request_id": "b" * 32,
                "expected_revision": "0",
                "content": "changed",
                "kind": "fact",
                "disclosure": "local",
                "confidence": "1",
            },
        )
    assert response.status_code == 403


def test_memory_retract_requires_confirmation_and_queues_lifecycle_action(tmp_path):
    database = tmp_path / "hina.sqlite3"
    item_id = _memory_database(database)
    app = create_app(_settings(tmp_path, database, write_enabled=True))

    with TestClient(app) as client:
        detail = client.get(f"/memory/{item_id}")
        assert detail.status_code == 200
        assert f'action="/memory/{item_id}/retract"' in detail.text
        assert "Retract memory" in detail.text

        missing_confirm = client.post(
            f"/memory/{item_id}/retract",
            data={
                "_csrf": app.state.admin_writer.csrf_token,
                "_request_id": "c" * 32,
                "expected_revision": "0",
            },
        )
        assert missing_confirm.status_code == 422

        response = client.post(
            f"/memory/{item_id}/retract",
            data={
                "_csrf": app.state.admin_writer.csrf_token,
                "_request_id": "d" * 32,
                "expected_revision": "0",
                "confirm": "yes",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith(f"/memory/{item_id}?queued=")

    db = sqlite3.connect(database)
    try:
        row = db.execute(
            """SELECT action,target,payload_json,status
               FROM admin_commands ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    finally:
        db.close()
    assert row[0] == "memory.item.retract"
    assert row[1] == str(item_id)
    assert row[3] == "pending"
    assert json.loads(row[2]) == {
        "item_id": item_id,
        "expected_revision": 0,
    }


def test_retracted_memory_detail_is_read_only_but_preserved(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store(str(database))
    scope = Scope(None, 10, 100)
    item_id = store.add_memory_item(
        scope,
        "keep provenance",
        kind="fact",
        disclosure="local",
        source_message_ids=("55",),
    )
    store.retract_memory_item(item_id, expected_revision=0)
    store.close()

    app = create_app(_settings(tmp_path, database, write_enabled=True))
    with TestClient(app) as client:
        response = client.get(f"/memory/{item_id}")

    assert response.status_code == 200
    assert "retracted" in response.text
    assert "runtime retrieval과 reconciliation에서 제외" in response.text
    assert "keep provenance" in response.text
    assert f'action="/memory/{item_id}/edit"' not in response.text
    assert f'action="/memory/{item_id}/retract"' not in response.text
