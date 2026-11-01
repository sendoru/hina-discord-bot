"""Dashboard annotation view, derived review queues and admin edits (#451)."""

import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.core.admin_commands import AdminCommand
from hina_bot.core.db_schema import initialize_connection
from hina_bot.core.memory_annotations import Annotator, Audience, UsageMode
from hina_bot.core.memory_items import MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.discord.admin_commands import execute_admin_command

PUBLIC = Scope(1, 10, 100, True)
FIELDS = {
    "time_scope": "ongoing", "stability": "long", "usage_mode": "ambient",
    "directive_type": "", "audience": "server", "importance": "2", "slot": "drink",
    "keywords": "커피, 카페", "event_at": "", "recurrence": "", "explicit": "true",
}


def _settings(tmp_path, database):
    return DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=True,
    )


def _annotate(store, item_id, **fields):
    annotation = parse_annotation({"evidence": "커피 좋아", "time_scope": "ongoing",
                                   "usage_mode": "contextual", **fields},
                                  MemoryKind.PREFERENCE, [{"user": "커피 좋아", "at": "2026-10-30 03:00:00"}])
    store.upsert_memory_annotation(replace(annotation, memory_item_id=item_id))


def _database(path):
    store = Store.create(str(path))
    initialize_connection(store.db, ("admin",))
    ids = {}
    for name in ("missing", "current", "stale", "flagged"):
        ids[name] = store.add_memory_item(PUBLIC, f"{name} memory", kind="preference",
                                          disclosure="local", confidence=0.9)
    ids["relationship"] = store.add_memory_item(PUBLIC, "relationship memory", kind="relationship",
                                                disclosure="implicit", confidence=0.9)
    # Retracted items are outside the backfill work list and its queues.
    ids["retracted"] = store.add_memory_item(PUBLIC, "retracted memory", kind="preference",
                                             disclosure="local", confidence=0.9)
    store.retract_memory_item(ids["retracted"], expected_revision=0)
    _annotate(store, ids["current"], slot="drink")
    _annotate(store, ids["stale"])
    _annotate(store, ids["flagged"], evidence="말한 적 없음")
    store.revise_memory_item(ids["stale"], expected_revision=0, content="stale memory edited",
                             kind="preference", disclosure="local", confidence=0.9)
    store.close()
    return ids


@pytest.mark.parametrize(("name", "state"), [
    ("missing", "missing"), ("current", "current"), ("stale", "stale"),
])
def test_detail_shows_annotation_state(tmp_path, name, state):
    database = tmp_path / "hina.sqlite3"
    ids = _database(database)
    app = create_app(_settings(tmp_path, database))
    with TestClient(app) as client:
        response = client.get(f"/memory/{ids[name]}")

    assert response.status_code == 200
    assert f'data-memory-annotation="{state}"' in response.text
    if state == "current":
        # The live status the tiered read path would compute right now.
        assert "Effective now" in response.text and "current" in response.text
    assert f'action="/memory/{ids[name]}/annotation"' in response.text


def test_relationship_detail_has_no_annotation_panel(tmp_path):
    database = tmp_path / "hina.sqlite3"
    ids = _database(database)
    app = create_app(_settings(tmp_path, database))
    with TestClient(app) as client:
        response = client.get(f"/memory/{ids['relationship']}")

    assert "data-memory-annotation" not in response.text


@pytest.mark.parametrize(("queue", "expected"), [
    ("missing", {"missing"}),
    ("stale", {"stale"}),
    ("flagged", {"flagged"}),
])
def test_derived_annotation_queues_filter_memory_list(tmp_path, queue, expected):
    database = tmp_path / "hina.sqlite3"
    ids = _database(database)
    app = create_app(_settings(tmp_path, database))
    with TestClient(app) as client:
        response = client.get("/memory", params={"annotation": queue})

    shown = {name for name, item_id in ids.items() if f">#{item_id}</a>" in response.text}
    assert shown == expected


def test_annotation_edit_enqueues_validated_fields_only(tmp_path):
    database = tmp_path / "hina.sqlite3"
    ids = _database(database)
    app = create_app(_settings(tmp_path, database))
    with TestClient(app) as client:
        bad = client.post(f"/memory/{ids['current']}/annotation", data={
            **FIELDS, "_csrf": app.state.admin_writer.csrf_token, "_request_id": "c" * 32,
            "expected_revision": "0", "usage_mode": "directive",
        })
        assert bad.status_code == 422
        relationship = client.post(f"/memory/{ids['relationship']}/annotation", data={
            **FIELDS, "_csrf": app.state.admin_writer.csrf_token, "_request_id": "d" * 32,
            "expected_revision": "0",
        })
        assert relationship.status_code == 409
        response = client.post(f"/memory/{ids['current']}/annotation", data={
            **FIELDS, "_csrf": app.state.admin_writer.csrf_token, "_request_id": "e" * 32,
            "expected_revision": "0",
        }, follow_redirects=False)
        assert response.status_code == 303

    db = sqlite3.connect(database)
    try:
        action, target, payload = db.execute(
            "SELECT action,target,payload_json FROM admin_commands ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        db.close()
    assert (action, target) == ("memory.annotation.set", str(ids["current"]))
    assert json.loads(payload) == {
        "item_id": ids["current"],
        "expected_revision": 0,
        "annotation": {
            "time_scope": "ongoing", "stability": "long", "event_at": None, "recurrence": None,
            "usage_mode": "ambient", "directive_type": None, "audience": "server",
            "importance": 2, "slot": "drink", "keywords": ["커피", "카페"], "explicit": True,
        },
    }


def _client(store):
    class Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return None

    return NS(store=store, memory_lock=lambda scope: Lock())


def _command(item_id, revision, **fields):
    return AdminCommand(
        id=9, request_id="f" * 32, actor="local-dashboard", action="memory.annotation.set",
        target=str(item_id),
        payload={"item_id": item_id, "expected_revision": revision,
                 "annotation": {**FIELDS, **fields}},
    )


@pytest.mark.asyncio
async def test_runtime_applies_admin_annotation_and_keeps_lifecycle_markers():
    store = Store.create(":memory:")
    item_id = store.add_memory_item(PUBLIC, "커피를 좋아한다.", kind="preference",
                                    disclosure="local", confidence=0.9)
    _annotate(store, item_id)
    store.reconfirm_memory_annotation(item_id, "2026-11-01 00:00:00")

    result = await execute_admin_command(_client(store), _command(item_id, 0))

    annotation = store.memory_annotation(item_id)
    assert result["usage_mode"] == "ambient" and result["audience"] == "server"
    assert annotation.annotator == Annotator.ADMIN and annotation.verified
    assert annotation.lint_flags == ()
    assert annotation.confirmations == 2 and annotation.evidence == "커피 좋아"
    # Automation never overwrites the admin judgment.
    _annotate(store, item_id, usage_mode="sensitive")
    assert store.memory_annotation(item_id).usage_mode == UsageMode.AMBIENT
    store.close()


@pytest.mark.asyncio
async def test_runtime_rejects_stale_revision_and_narrows_private_audience():
    store = Store.create(":memory:")
    private = Scope(1, 10, 100, False)
    item_id = store.add_memory_item(private, "비공개 채널 기억", kind="fact",
                                    disclosure="local", confidence=0.9)

    with pytest.raises(ValueError, match="changed"):
        await execute_admin_command(_client(store), _command(item_id, 3))
    await execute_admin_command(_client(store), _command(item_id, 0))

    annotation = store.memory_annotation(item_id)
    assert annotation.audience == Audience.OWNER
    assert annotation.observed_at == store.memory_item(item_id).created_at
    store.close()
