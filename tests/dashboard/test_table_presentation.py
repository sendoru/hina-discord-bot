import re
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.dashboard.repository import AdminRepository
from hina_bot.dashboard.services import MemoryService, ReconciliationService
from hina_bot.dashboard.telemetry import TelemetryReader


def test_shared_summary_cursor_and_origin_components_preserve_stored_data(tmp_path):
    database = tmp_path / "tables.sqlite3"
    store = Store(str(database))
    guild = Scope(1489432315523502251, 1554321651473195059, 221977862859653120, True)
    dm = Scope(None, 456789012345678901, 221977862859653125)
    for index, scope in enumerate((guild, dm), 1):
        store.add(scope, index, "question", "reply", name=f"User {index}")
        store.save_memory_extraction_cursor(scope, store.history(scope)[-1]["id"])
    store.add_shared_call(guild, 3, "User 1", "shared question")
    shared_id = int(store.db.execute(
        "SELECT id FROM shared_calls WHERE message_id='3'"
    ).fetchone()["id"])
    store.save_shared_summary(guild, "User 1", "Shared summary", shared_id)
    long_text = ("사용자는 히나에게 직접적인 애정과 호감을 표현합니다. " * 15).strip()
    old = store.add_memory_item(guild, long_text, kind="relationship", disclosure="implicit")
    new = store.add_memory_item(guild, long_text, kind="relationship", disclosure="implicit")
    store.add_memory_reconciliation_proposal(
        guild, new_memory_item_id=new, target_memory_item_id=old,
        relation="duplicate", confidence=0.9, source_message_ids=("1",),
    )
    store.close()
    before = database.read_bytes()

    repository = AdminRepository(database)
    memory = MemoryService(repository)
    summaries = memory.summaries()
    assert len(summaries["shared"]) == 1
    assert summaries["shared"][0]["scope_display"].raw == guild.conversation
    assert summaries["shared"][0]["through_id"] == shared_id

    cursors = memory.extraction_cursors()
    assert {row["scope_display"].raw for row in cursors["rows"]} == {
        guild.conversation,
        dm.conversation,
    }
    usage_path = str(tmp_path / "missing-usage.jsonl")
    event_path = str(tmp_path / "missing-events.jsonl")
    reconciliation = ReconciliationService(repository, TelemetryReader(usage_path, event_path))
    proposal = reconciliation.reconciliation_proposals()["rows"][0]
    assert proposal["origin_display"].raw == guild.realm
    assert proposal["origin_channel_id"] == str(guild.channel_id)
    assert proposal["target_content"] == proposal["new_content"] == long_text
    assert proposal["user_id"] == str(guild.user_id)

    with TestClient(create_app(DashboardSettings(
        database_path=str(database), usage_log_path=usage_path, event_log_path=event_path,
    ))) as client:
        response = client.get("/summaries")
        tables = [ElementTree.fromstring(table) for table in re.findall(
            r'<table class="wide-table summary-table">.*?</table>', response.text, re.DOTALL,
        )]
        assert len(tables) == 1
        table = tables[0]
        assert [th.text for th in table.findall("thead/tr/th")][:3] == [
            "Realm", "Channel", "User",
        ]
        assert len(table.findall("thead/tr/th")) == 6
        for row in table.findall("tbody/tr"):
            if row.get("class") == "content-row":
                assert row.find("td").get("colspan") == "6"
            else:
                assert len(row.findall("td")) == 6
        assert "Shared summary" in response.text
        assert "Personal summaries" not in response.text

        cursor_response = client.get("/memory/cursors")
        assert "<th>Realm</th><th>Channel</th><th>User</th>" in cursor_response.text
        assert "Summary through" not in cursor_response.text
        assert guild.conversation not in cursor_response.text
        assert dm.conversation not in cursor_response.text

        review = client.get("/reconciliation")
        assert long_text in review.text
        assert '<code class="scope-id" data-copy-text="1489432315523502251">1489432315523502251</code>' in review.text
        assert '<code class="scope-id" data-copy-text="1554321651473195059">1554321651473195059</code>' in review.text
        assert 'class="scope-raw-key"' not in review.text
        assert f'href="/memory/{old}"' in review.text
        assert f'href="/memory/{new}"' in review.text
    assert database.read_bytes() == before


@pytest.mark.parametrize("guild_id", [1489432315523502251, None])
def test_raw_scope_keys_are_limited_to_diagnostic_views(tmp_path, guild_id):
    database = tmp_path / "all-views.sqlite3"
    store = Store(str(database))
    scope = Scope(guild_id, 1554321651473195059, 221977862859653120, True)
    old = store.add_memory_item(
        scope, "Relationship old", kind="relationship", disclosure="implicit",
        source_message_ids=("55",), user_name="Stored user",
        confidence=0.9, relationship_evidence={"familiarity": 2},
    )
    new = store.add_memory_item(
        scope, "Relationship new", kind="relationship", disclosure="implicit",
        source_message_ids=("55",), user_name="Stored user",
        confidence=0.9, relationship_evidence={"familiarity": 2},
    )
    token = CURRENT_TURN_ID.set("scope-trace")
    try:
        store.add(
            scope,
            55,
            "Source input",
            "Source reply",
            name="Stored user",
            context_provenance={
                "structured_memory": [{
                    "item_id": old,
                    "origin_realm": scope.realm,
                    "origin_channel_id": str(scope.channel_id),
                    "access": "full",
                }],
            },
        )
    finally:
        CURRENT_TURN_ID.reset(token)
    proposal = store.add_memory_reconciliation_proposal(
        scope,
        new_memory_item_id=new,
        target_memory_item_id=old,
        relation="duplicate",
        confidence=0.9,
        source_message_ids=("55",),
    )
    store.set_note(scope.user_note, "User note")
    store.close()
    before = database.read_bytes()

    target = {"target_user_id": str(scope.user_id)}
    if guild_id:
        target.update(
            target_guild_id=str(guild_id),
            target_channel_id=str(scope.channel_id),
        )
    else:
        target.update(target_scope_type="dm")

    with TestClient(create_app(DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "no-usage.jsonl"),
        event_log_path=str(tmp_path / "no-events.jsonl"),
    ))) as client:
        for path in [
            "/conversations",
            "/conversations/1/context",
            f"/memory/{old}",
            f"/reconciliation/{proposal}",
        ]:
            response = client.get(path)
            assert response.status_code == 200
            assert "<dt>Realm</dt>" in response.text
            assert "<dt>Channel</dt>" in response.text
            assert "<dt>User</dt>" in response.text
            assert f'<code class="scope-id" data-copy-text="{scope.channel_id}">{scope.channel_id}</code>' in response.text
            assert "Stored user" in response.text
            assert 'class="scope-raw-key"' not in response.text

        trace = client.get("/traces/scope-trace")
        assert trace.status_code == 200
        assert f'<code class="scope-raw-key">{scope.conversation}</code>' in trace.text
        assert "Raw scope" in trace.text

        state = client.get("/state", params=target)
        assert state.status_code == 200
        assert "<dt>Realm</dt>" in state.text
        assert "User note" in state.text
        assert f'<code class="scope-raw-key">{scope.user_note}</code>' in state.text
        assert "Raw scope" in state.text

        for params in [
            {"target_guild_id": "99", "target_channel_id": "999"},
            {"target_scope_type": "dm"},
        ]:
            response = client.get("/relationships", params=params)
            assert response.status_code == 200
            assert f'<code class="scope-id" data-copy-text="{scope.channel_id}">{scope.channel_id}</code>' in response.text
            assert 'class="scope-raw-key"' not in response.text

    assert database.read_bytes() == before
