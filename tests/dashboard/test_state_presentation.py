import re
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.dashboard.repository import AdminRepository
from hina_bot.dashboard.services import ContextStateService

UNKNOWN_KEY = 'legacy:<script>alert("scope")</script>'


def seed_state(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = Store(str(database))
    guild = Scope(1, 10, 100)
    dm = Scope(None, 20, 100)
    store.add(guild, 1, "guild question", "reply", name="Guild User")
    store.add(dm, 2, "dm question", "reply", name="DM User")
    store.set_memory_mode_override("global", "normal")
    store.set_memory_mode_override(guild.realm, "read_only")
    store.set_memory_mode_override(guild.channel, "off")
    store.set_memory_mode_override(dm.channel, "write_only")
    store.set_memory_mode_override(UNKNOWN_KEY, "off")
    store.set_chat_log_mode_override(guild.channel, "off")
    store.set_note("config:chatlog_capture:global", "direct")
    store.set_note(f"config:chatlog_capture:{dm.channel}", "all")
    store.set_note("config:chatlog_unified_v1", "1")
    store.set_note(guild.realm, "server note")
    store.set_note(guild.user_note, "guild user note")
    store.set_note(dm.user_note, "dm user note")
    store.set_note(guild.channel, "channel note remains other")
    store.set_note(guild.conversation, "conversation note remains other")
    store.set_note(dm.realm, "dm realm note remains other")
    store.set_note("global", "global note remains other")
    store.set_note(UNKNOWN_KEY, "<b>unknown note</b>")
    store.close()
    return database


def test_state_presentation_preserves_note_policy_search_and_stored_overrides(tmp_path):
    database = seed_state(tmp_path)
    before = database.read_bytes()
    service = ContextStateService(AdminRepository(database))
    data = service.context_state()
    memory = {row["scope"]: row for row in data["memory_overrides"]}
    assert memory["dm:100:channel:20"]["scope_display"].realm_id == "100"
    assert memory["dm:100:channel:20"]["scope_display"].user_id == ""
    assert memory["dm:100:channel:20"]["mode"] == "write_only"
    assert memory[UNKNOWN_KEY]["scope_display"].raw == UNKNOWN_KEY
    assert memory[UNKNOWN_KEY]["scope_display"].level == "unknown"
    chat = {row["scope"]: row for row in data["chat_overrides"]}
    assert set(chat) == {"global", "guild:1:channel:10", "dm:100:channel:20"}
    assert chat["global"]["enabled"] is None
    assert chat["global"]["capture"] == "direct"
    assert chat["guild:1:channel:10"]["enabled"] == "off"
    assert chat["guild:1:channel:10"]["capture"] is None
    assert chat["dm:100:channel:20"]["capture"] == "all"
    assert data["internal_note_count"] == 3
    notes = {row["scope"]: row for row in data["manual_notes"]}
    assert notes["guild:1"]["kind"] == "server"
    assert notes["guild:1:user:100"]["kind"] == "user"
    assert notes["guild:1:user:100"]["user_name"] == "Guild User"
    assert notes["dm:100:user:100"]["kind"] == "user"
    assert notes["dm:100:user:100"]["user_name"] == "DM User"
    for key in ("guild:1:channel:10", "guild:1:channel:10:user:100", "dm:100", "global",
                UNKNOWN_KEY):
        assert notes[key]["kind"] == "other"
        assert notes[key]["realm"] == notes[key]["user_id"] == notes[key]["user_name"] == ""
    assert not any(key.startswith("config:") for key in notes)
    for query, expected in (
        ("dm user", {"dm:100:user:100"}),
        ("guild:1:user:100", {"guild:1:user:100"}),
        ("server", {"guild:1"}),
        ("conversation note", {"guild:1:channel:10:user:100"}),
        (UNKNOWN_KEY, {UNKNOWN_KEY}),
    ):
        filtered = service.context_state(query=query)
        assert {row["scope"] for row in filtered["manual_notes"]} == expected
        assert filtered["memory_overrides"] == data["memory_overrides"]
        assert filtered["chat_overrides"] == data["chat_overrides"]
    assert database.read_bytes() == before


def test_state_tables_render_components_raw_keys_and_unknowns_safely(tmp_path):
    database = seed_state(tmp_path)
    with TestClient(create_app(DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "missing-usage.jsonl"),
        event_log_path=str(tmp_path / "missing-events.jsonl"),
    ))) as client:
        response = client.get("/state")
    assert response.status_code == 200
    assert UNKNOWN_KEY not in response.text
    assert "<b>unknown note</b>" not in response.text
    tables = [ElementTree.fromstring(value) for value in re.findall(
        r'<table class="state-inventory(?: state-notes)?"[^>]*>.*?</table>',
        response.text,
        re.DOTALL,
    )]
    assert len(tables) == 3
    assert [[cell.text for cell in table.findall("thead/tr/th")] for table in tables] == [
        ["Realm", "Channel", "Mode"],
        ["Realm", "Channel", "Enabled override", "Capture override"],
        ["Kind", "Realm", "User", "Content"],
    ]

    def rows_by_key(table, realm_column):
        rows = {}
        for row in table.findall("tbody/tr"):
            cells = row.findall("td")
            key_cell = cells[realm_column]
            key = key_cell.find("details/code")
            if key is None:
                key = key_cell.find("code")
            rows[key.text] = cells
        return rows

    memory = rows_by_key(tables[0], 0)
    assert memory["global"][0].find("div/span").text == "Global"
    assert memory["global"][1].text.strip() == "—"
    assert memory["guild:1"][0].find("div/span").text == "Guild"
    assert memory["guild:1"][0].find("div/code").text == "1"
    assert memory["dm:100:channel:20"][0].find("div/span").text == "DM · user"
    assert memory["dm:100:channel:20"][0].find("div/code").text == "100"
    assert memory["dm:100:channel:20"][1].find("code").text == "20"
    assert memory["dm:100:channel:20"][0].find("details/summary").text == "Raw scope"
    assert memory[UNKNOWN_KEY][0].find("div/span").text == "Unknown"
    assert memory[UNKNOWN_KEY][0].find("details") is None
    chat = rows_by_key(tables[1], 0)
    assert chat["guild:1:channel:10"][2].text.strip() == "off"
    assert chat["guild:1:channel:10"][3].text.strip() == "inherit"
    assert chat["dm:100:channel:20"][2].text.strip() == "inherit"
    assert chat["dm:100:channel:20"][3].text.strip() == "all"
    notes = rows_by_key(tables[2], 1)
    assert notes["guild:1"][2].text.strip() == "—"
    assert notes["dm:100:user:100"][2].find("div").text == "DM User"
    assert notes["dm:100:user:100"][2].find("code").text == "100"
    assert (
        notes["guild:1:channel:10:user:100"][1].find("details/summary").text
        == "Raw scope"
    )
    assert notes[UNKNOWN_KEY][3].find("div").text == "<b>unknown note</b>"
