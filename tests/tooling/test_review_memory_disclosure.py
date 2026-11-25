"""Offline disclosure reclassification: proposal, explicit approval and CAS safety."""
import asyncio
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.core.memory_annotations import Annotator
from hina_bot.core.memory_items import MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.review_memory_disclosure import apply_plan, build_plan, main

DM = Scope(None, 10, 100)
SERVER = Scope(1, 20, 100, True)


def _seed(store, message_id=501, *, scope=DM, disclosure="local", usage="ambient"):
    quote = f"재즈를 즐겨 들어 {message_id}"
    store.add(scope, message_id, quote, "reply")
    item_id = store.add_memory_item(
        scope, "재즈를 즐겨 들음", kind="preference", disclosure=disclosure,
        source_message_ids=(str(message_id),), confidence=0.9,
    )
    annotation = parse_annotation(
        {"evidence": quote, "time_scope": "ongoing", "stability": "long",
         "usage_mode": usage, "audience": "owner"},
        MemoryKind.PREFERENCE, [{"user": quote, "at": "2026-10-01 00:00:00"}],
    )
    store.upsert_memory_annotation(replace(annotation, memory_item_id=item_id))
    return item_id


def _plan(store):
    return asyncio.run(build_plan(store))


def _approve(row):
    row.update(approved=True, new="global")
    return row


def test_preview_is_content_free_and_only_explicitly_approved_row_changes():
    with_store = Store.create(":memory:")
    try:
        first = _seed(with_store)
        second = _seed(with_store, 502)
        before = with_store.memory_item(first)
        annotation = with_store.memory_annotation(first)
        plan = _plan(with_store)
        assert "재즈" not in json.dumps(plan, ensure_ascii=False)
        assert all(not row["approved"] and not row["blockers"] for row in plan["items"])
        assert apply_plan(with_store, plan) == {}
        _approve(plan["items"][0])
        assert apply_plan(with_store, plan) == {"applied": 1}
        after = with_store.memory_item(first)
        assert after.disclosure.value == "global"
        assert after.revision == before.revision + 1
        assert replace(after, disclosure=before.disclosure, revision=before.revision,
                       updated_at=before.updated_at) == before
        assert with_store.memory_annotation(first) == replace(
            annotation, item_revision=after.revision,
        )
        assert with_store.memory_item_edit_history(first)[0]["disclosure"] == "local"
        assert with_store.memory_item(second).disclosure.value == "local"
        assert apply_plan(with_store, plan) == {"changed_or_missing": 1}
        assert len(with_store.memory_item_edit_history(first)) == 1
    finally:
        with_store.close()


def test_missing_source_sensitive_admin_and_disabled_are_not_approvable():
    store = Store.create(":memory:")
    try:
        missing = _seed(store)
        sensitive = _seed(store, 502, usage="sensitive")
        admin = _seed(store, 503)
        disabled = _seed(store, 504, scope=SERVER)
        wrong_owner = _seed(store, 505)
        store.upsert_memory_annotation(replace(
            store.memory_annotation(admin), annotator=Annotator.ADMIN,
        ))
        store.set_memory_mode_override(SERVER.channel, "off")
        with store.db:
            store.db.execute("DELETE FROM turns WHERE message_id='501'")
            store.db.execute("UPDATE turns SET user_id='900' WHERE message_id='505'")
        plan = _plan(store)
        rows = {row["item_id"]: row for row in plan["items"]}
        assert "missing_source" in rows[missing]["blockers"]
        assert "sensitive" in rows[sensitive]["blockers"]
        assert "admin_or_previously_edited" in rows[admin]["blockers"]
        assert "memory_writes_disabled" in rows[disabled]["blockers"]
        assert "missing_source" in rows[wrong_owner]["blockers"]
        for row in rows.values():
            _approve(row)
        assert apply_plan(store, plan) == {"blocked_or_invalid": 5}
        assert all(store.memory_item(item_id).disclosure.value == "local" for item_id in rows)
    finally:
        store.close()


def test_source_or_annotation_change_invalidates_review():
    store = Store.create(":memory:")
    try:
        first = _seed(store)
        second = _seed(store, 502)
        plan = _plan(store)
        for row in plan["items"]:
            _approve(row)
        with store.db:
            store.db.execute("DELETE FROM turns WHERE message_id='501'")
        store.upsert_memory_annotation(replace(
            store.memory_annotation(second), importance=3,
        ))
        assert apply_plan(store, plan) == {"changed_or_missing": 2}
        assert store.memory_item(first).disclosure.value == "local"
        assert store.memory_item(second).disclosure.value == "local"
    finally:
        store.close()


def test_atomic_rollback_on_later_failure():
    store = Store.create(":memory:")
    try:
        first = _seed(store)
        second = _seed(store, 502)
        plan = _plan(store)
        for row in plan["items"]:
            _approve(row)
        store.db.execute(f"""CREATE TRIGGER abort_disclosure BEFORE UPDATE ON memory_items
            WHEN OLD.id={second} BEGIN SELECT RAISE(ABORT, 'test'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            apply_plan(store, plan)
        assert store.memory_item(first).disclosure.value == "local"
        assert store.memory_item(second).disclosure.value == "local"
        assert not store.memory_item_edit_history(first)
        assert store.memory_annotation(first).item_revision == 0
    finally:
        store.close()


def test_new_global_duplicate_remains_reviewable_without_automatic_promotion():
    store = Store.create(":memory:")
    try:
        target = _seed(store)
        wider = _seed(store, 502, disclosure="global")
        proposal = store.add_memory_reconciliation_proposal(
            DM, new_memory_item_id=wider, target_memory_item_id=target,
            relation="duplicate", confidence=0.99,
        )
        assert store.apply_memory_reconciliation_proposal(proposal) == "applied"
        assert store.memory_item(target).disclosure.value == "local"
        plan = _plan(store)
        assert len(plan["items"]) == 1
        row = plan["items"][0]
        assert row["duplicate_disclosure_review"] == [
            {"item_id": wider, "disclosure": "global"},
        ]
        assert not row["approved"]
        assert apply_plan(store, plan) == {}
        _approve(row)
        assert apply_plan(store, plan) == {"applied": 1}
        assert store.memory_item(target).disclosure.value == "global"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_opt_in_suggestion_is_not_approval_and_skips_blocked_sources():
    store = Store.create(":memory:")
    try:
        first = _seed(store)
        _seed(store, 502, usage="sensitive")
        worker = NS(settings=NS(
            model="m", model_routing_mode="fixed", memory_routing_smart_threshold=2.0,
            memory_output_tokens=1024, provider="openai", runtime_timezone="Asia/Seoul",
            gemini_thinking_level="low",
        ), _memory_request=AsyncMock(return_value=NS(
            status="completed", output_text='{"disclosure":"global","reason":"portable"}',
        )))
        plan = await build_plan(store, worker=worker)
        worker._memory_request.assert_awaited_once()
        assert plan["items"][0]["new"] == "global"
        assert not plan["items"][0]["approved"]
        assert plan["items"][1]["new"] is None
        assert apply_plan(store, plan) == {}
        assert store.memory_item(first).disclosure.value == "local"
    finally:
        store.close()


def test_cli_read_only_preview_and_confirmed_backup(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store.create(str(path))
    item = _seed(store)
    store.close()
    plan_path = tmp_path / "review.json"
    assert main(["--database", str(path), "--plan-out", str(plan_path)]) == 0
    assert plan_path.stat().st_mode & 0o077 == 0
    plan = json.loads(plan_path.read_text())
    _approve(plan["items"][0])
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(SystemExit):
        main(["--database", str(path), "--apply", str(plan_path)])
    backup_dir = tmp_path / "backups"
    assert main(["--database", str(path), "--apply", str(plan_path),
                 "--offline-confirmed", "--backup-dir", str(backup_dir)]) == 0
    (backup,) = list(backup_dir.glob("*.sqlite3"))
    old = Store.open_existing(str(backup))
    new = Store.open_existing(str(path))
    try:
        assert old.memory_item(item).disclosure.value == "local"
        assert new.memory_item(item).disclosure.value == "global"
    finally:
        old.close()
        new.close()
