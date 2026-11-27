import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from hina_bot.core.admin_commands import AdminCommand
from hina_bot.core.config import Settings
from hina_bot.core.db_schema import initialize_connection
from hina_bot.core.runtime_config import RUNTIME_SETTING_SPECS, RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.dashboard.routes.admin_runtime import RUNTIME_CONFIG_SECTIONS, _runtime_sections
from hina_bot.discord.admin_commands import execute_admin_command


@pytest.mark.asyncio
async def test_runtime_admin_command_uses_runtime_settings_and_side_effects():
    store = Store.create(":memory:")
    settings = RuntimeSettings(
        Settings(discord_token="test", channel_context_chars=6000),
        store,
    )
    recent = NS(budget=6000, clear_all=Mock())
    client = NS(settings=settings, recent=recent)

    result = await execute_admin_command(
        client,
        AdminCommand(
            id=1,
            request_id="a" * 32,
            actor="local-dashboard",
            action="runtime.set",
            target="channel_context_chars",
            payload={"key": "channel_context_chars", "value": "4200"},
        ),
    )
    assert settings.channel_context_chars == 4200
    assert recent.budget == 4200
    assert result["source"] == "db"

    result = await execute_admin_command(
        client,
        AdminCommand(
            id=2,
            request_id="b" * 32,
            actor="local-dashboard",
            action="runtime.reset",
            target="channel_context_chars",
            payload={"key": "channel_context_chars"},
        ),
    )
    assert settings.channel_context_chars == 6000
    assert recent.budget == 6000
    assert result["source"] == "startup"
    store.close()


@pytest.mark.asyncio
async def test_recent_window_runtime_change_invalidates_hydration_state():
    store = Store.create(":memory:")
    settings = RuntimeSettings(Settings(discord_token="test"), store)
    recent = NS(ttl=3600, clear_all=Mock())
    client = NS(settings=settings, recent=recent)
    try:
        result = await execute_admin_command(
            client,
            AdminCommand(
                id=3, request_id="e" * 32, actor="local-dashboard",
                action="runtime.set", target="recent_context_window_minutes",
                payload={"key": "recent_context_window_minutes", "value": "90"},
            ),
        )
        assert result["source"] == "db"
        assert recent.ttl == 5400
        recent.clear_all.assert_called_once()

        result = await execute_admin_command(
            client,
            AdminCommand(
                id=4, request_id="f" * 32, actor="local-dashboard",
                action="runtime.reset", target="recent_context_window_minutes",
                payload={"key": "recent_context_window_minutes"},
            ),
        )
        assert result["source"] == "startup"
        assert recent.ttl == 3600
        assert recent.clear_all.call_count == 2
    finally:
        store.close()


def test_runtime_config_sections_cover_all_current_settings_and_fallback():
    classified = [
        key
        for _, _, _, keys in RUNTIME_CONFIG_SECTIONS
        for key in keys
    ]
    assert len(classified) == len(set(classified))
    assert set(classified).issubset(RUNTIME_SETTING_SPECS)

    rows = [{"key": key} for key in RUNTIME_SETTING_SPECS]
    rows.append({"key": "new_unclassified_setting"})
    sections = _runtime_sections(rows)
    assert [section["id"] for section in sections] == [
        *(section_id for section_id, _, _, _ in RUNTIME_CONFIG_SECTIONS),
        "other",
    ]
    assert sections[-1]["rows"] == [{"key": "new_unclassified_setting"}]
    assert sum(len(section["rows"]) for section in sections) == len(rows)


def test_runtime_dashboard_post_only_enqueues_command(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store.create(str(database))
    initialize_connection(store.db, ("admin",))
    RuntimeSettings(Settings(discord_token="test"), store)
    store.close()
    settings = DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=True,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/admin/runtime/set",
            data={
                "_csrf": app.state.admin_writer.csrf_token,
                "_request_id": "c" * 32,
                "key": "channel_context_chars",
                "value": "4300",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303

    db = sqlite3.connect(database)
    row = db.execute(
        "SELECT action,target,payload_json,status FROM admin_commands"
    ).fetchone()
    assert row == (
        "runtime.set",
        "channel_context_chars",
        '{"key":"channel_context_chars","value":"4300"}',
        "pending",
    )
    assert db.execute("SELECT COUNT(*) FROM runtime_config").fetchone()[0] == 0
    db.close()



def test_runtime_dashboard_edits_overrides_inline(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store.create(str(database))
    initialize_connection(store.db, ("admin",))
    settings = RuntimeSettings(
        Settings(
            discord_token="test",
            call_prefixes=("히나야", "히나"),
            always_reply_channel_ids=frozenset({1554321651473195059, 1555043087308598066}),
            channel_context_chars=6200,
            history_max_chars=9876,
        ),
        store,
    )
    settings.capture_startup_snapshot()
    store.settings.set_runtime_value("personal_memory_read_mode", '"shadow"')
    settings.set_text("channel_context_chars", "4300")
    settings.set_text("CALL_PREFIXES", "히나야, 히나 , 선도부장")
    store.close()
    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(tmp_path / "usage.jsonl"),
            event_log_path=str(tmp_path / "events.jsonl"),
        )
    )

    with TestClient(app) as client:
        response = client.get("/admin/runtime")

    assert response.status_code == 200
    assert 'class="runtime-config-table"' in response.text
    assert 'data-runtime-config-search-input' in response.text
    assert 'data-runtime-config-no-results' in response.text
    assert 'id="runtime-config-section-memory"' in response.text
    assert 'id="runtime-config-section-logging"' in response.text
    assert response.text.count('class="runtime-config-section"') == len(RUNTIME_CONFIG_SECTIONS)
    assert response.text.count('data-runtime-config-setting-row') == len(RUNTIME_SETTING_SPECS)

    assert 'data-runtime-config-help-toggle="channel_context_chars"' in response.text
    assert 'id="runtime-config-help-channel_context_chars"' in response.text
    assert 'data-runtime-config-help-row="channel_context_chars"' in response.text
    assert "최근 서버 채널 문맥에서 외부 모델 요청에 사용할 본문 문자 수 상한" in response.text
    assert "<strong>Type</strong> <code>integer</code>" in response.text
    assert "range: 0–12000" in response.text
    assert "none / null / off / - clears the value" in response.text
    assert "<th>Set</th>" not in response.text
    assert 'class="runtime-config-action-header"' in response.text
    assert "data-runtime-config-action-view" in response.text
    assert "data-runtime-config-action-edit" in response.text
    assert 'form="runtime-config-form-channel_context_chars"' in response.text
    assert 'colspan="5"' in response.text
    assert "data-runtime-config-editor" in response.text
    assert "data-runtime-config-form" in response.text
    assert "Set override" in response.text
    assert "Edit" in response.text
    assert 'value="4300"' in response.text
    assert 'aria-label="Override value for CHANNEL_CONTEXT_CHARS"' in response.text
    assert 'id="runtime-dm_always_reply"' in response.text
    assert '<option value="off"' in response.text
    assert '<option value="on"' in response.text
    assert 'id="runtime-external_context_policy"' in response.text
    assert '<option value="full" selected' in response.text
    assert '<option value="decision_shadow"' not in response.text
    assert '<option value="shadow"' not in response.text
    assert '<option value="bot_interactions_only"' in response.text
    assert '<option value="full"' in response.text
    assert 'id="runtime-decision_timeout_seconds"' in response.text
    assert 'aria-label="Override value for DECISION_TIMEOUT_SECONDS"' in response.text
    assert 'id="runtime-gemini_thinking_level"' in response.text
    assert '<option value="minimal"' in response.text
    assert '<option value="high"' in response.text
    assert 'id="runtime-channel_context_chars"' in response.text
    assert 'id="runtime-recent_context_window_minutes"' in response.text
    assert 'id="runtime-conversation_session_gap_minutes"' in response.text
    assert 'aria-label="Override value for CONVERSATION_SESSION_GAP_MINUTES"' in response.text
    assert 'aria-label="Override value for RECENT_CONTEXT_WINDOW_MINUTES"' in response.text
    assert 'type="number"' in response.text
    assert 'min="0"' in response.text
    assert 'max="12000"' in response.text
    assert 'step="1"' in response.text
    assert 'id="runtime-model_routing_smart_threshold"' in response.text
    assert 'min="0.1"' in response.text
    assert 'max="10.0"' in response.text
    assert 'step="any"' in response.text
    assert 'id="runtime-model"' in response.text
    assert 'id="runtime-answer_timeout_seconds"' in response.text
    assert 'aria-label="Override value for ANSWER_TIMEOUT_SECONDS"' in response.text
    assert 'maxlength="200"' in response.text
    assert "startup 6200" in response.text
    assert ">9876</code>" in response.text
    assert 'value="9876"' in response.text
    assert 'data-runtime-collection-manage="call_prefixes"' in response.text
    assert 'data-runtime-collection-row="call_prefixes"' in response.text
    assert 'data-kind="prefixes"' in response.text
    assert 'data-max-items="20"' in response.text
    assert 'data-empty-allowed="false"' in response.text
    assert 'data-runtime-collection-manage="always_reply_channel_ids"' in response.text
    assert 'data-kind="discord_ids"' in response.text
    assert 'data-max-items="100"' in response.text
    assert 'data-empty-allowed="true"' in response.text
    assert "선도부장" in response.text
    assert 'data-value="히나 "' in response.text
    assert '히나␠' in response.text
    assert '공백은 ␠로 표시' in response.text
    assert "1554321651473195059" in response.text
    assert "Startup baseline" in response.text
    assert "runtime-config.js" in response.text
    assert "runtime-config-help-toggle" in response.text



def test_runtime_collection_dashboard_posts_full_desired_value(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store.create(str(database))
    initialize_connection(store.db, ("admin",))
    RuntimeSettings(
        Settings(
            discord_token="test",
            call_prefixes=("히나야",),
        ),
        store,
    )
    store.close()
    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(tmp_path / "usage.jsonl"),
            event_log_path=str(tmp_path / "events.jsonl"),
            write_enabled=True,
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/admin/runtime/set",
            data={
                "_csrf": app.state.admin_writer.csrf_token,
                "_request_id": "d" * 32,
                "key": "call_prefixes",
                "value": "히나야, 히나 , 선도부장 ",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    db = sqlite3.connect(database)
    row = db.execute(
        "SELECT action,target,payload_json,status FROM admin_commands"
    ).fetchone()
    assert row == (
        "runtime.set",
        "call_prefixes",
        '{"key":"call_prefixes","value":"히나야, 히나 , 선도부장 "}',
        "pending",
    )
    db.close()


@pytest.mark.asyncio
async def test_runtime_reset_all_applies_side_effects():
    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(Settings(discord_token="test"), store)
        settings.set_text("channel_context_chars", "4300")
        settings.set_text("recent_context_window_minutes", "90")
        recent = NS(budget=4300, ttl=5400, clear_all=Mock())
        result = await execute_admin_command(
            NS(settings=settings, recent=recent),
            AdminCommand(1, "a" * 32, "local-dashboard", "runtime.reset_all", "all", {}),
        )
        assert result == {"cleared": 2, "source": "startup"}
        assert recent.budget == settings.base.channel_context_chars
        assert recent.ttl == settings.base.recent_context_window_minutes * 60
        recent.clear_all.assert_called_once()
    finally:
        store.close()


def test_runtime_reset_all_dashboard_requires_confirmation_and_enqueues(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store.create(str(database))
    initialize_connection(store.db, ("admin",))
    settings = RuntimeSettings(Settings(discord_token="test"), store)
    settings.capture_startup_snapshot()
    settings.set_text("channel_context_chars", "4300")
    store.settings.set_runtime_value("obsolete", "invalid")
    store.close()
    app = create_app(DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=True,
    ))
    with TestClient(app) as client:
        assert "Clear All Overrides (2)" in client.get("/admin/runtime").text
        form = {"_csrf": app.state.admin_writer.csrf_token, "_request_id": "a" * 32}
        url = "/admin/runtime/reset-all"
        assert client.post(url, data=form).status_code == 422
        assert client.post(url, data={**form, "confirm": "yes", "_csrf": "bad"}).status_code == 403
        response = client.post(url, data={**form, "confirm": "yes"}, follow_redirects=False)
        assert response.status_code == 303
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT action,target,status FROM admin_commands").fetchone() == (
            "runtime.reset_all", "all", "pending",
        )
        assert db.execute("SELECT COUNT(*) FROM runtime_config").fetchone()[0] == 2


def test_runtime_dashboard_json_summary_file_baseline_and_override(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store.create(str(database))
    initialize_connection(store.db, ("admin",))
    try:
        runtime = RuntimeSettings(
            Settings(
                discord_token="test",
                openrouter_provider_preferences={"sort": "latency"},
                openrouter_provider_preferences_file="config/preferences.json",
            ),
            store,
        )
        runtime.capture_startup_snapshot()
        runtime.set_text("OPENROUTER_PROVIDER_PREFERENCES", '{"sort":"price","zdr":true}')
    finally:
        store.close()
    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(tmp_path / "usage.jsonl"),
            event_log_path=str(tmp_path / "events.jsonl"),
        )
    )
    with TestClient(app) as client:
        response = client.get("/admin/runtime")
    assert response.status_code == 200
    assert "JSON · 2 properties" in response.text
    assert "Startup (file) · 1 properties" in response.text
    assert '<textarea id="runtime-openrouter_provider_preferences"' in response.text
    assert "data-runtime-json-format" in response.text


@pytest.mark.parametrize(
    ("path", "fields", "action", "target"),
    [
        ("set", {"key": "channel_context_chars", "value": "4300"}, "runtime.set", "channel_context_chars"),
        ("reset", {"key": "channel_context_chars"}, "runtime.reset", "channel_context_chars"),
        ("reset-all", {"confirm": "yes"}, "runtime.reset_all", "all"),
    ],
)
def test_runtime_dashboard_async_submission(tmp_path, path, fields, action, target):
    database = tmp_path / "hina.sqlite3"
    store = Store.create(str(database))
    initialize_connection(store.db, ("admin",))
    store.close()
    app = create_app(DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=True,
    ))
    payload = {
        "_csrf": app.state.admin_writer.csrf_token,
        "_request_id": "a" * 32,
        **fields,
    }
    with TestClient(app) as client:
        response = client.post(
            f"/admin/runtime/{path}", data=payload,
            headers={"accept": "application/json"}, follow_redirects=False,
        )
        assert response.status_code == 202
        command_id = response.json()["command_id"]
        status = client.get(f"/admin/commands/{command_id}").json()
        assert status["status"] == "pending"
        assert status["action"] == action
        assert status["target"] == target
        repeated = client.post(
            f"/admin/runtime/{path}", data=payload,
            headers={"accept": "application/json"},
        )
        assert repeated.status_code == 202
        assert repeated.json()["command_id"] == command_id
        invalid = client.post(
            f"/admin/runtime/{path}", data={**payload, "_csrf": "invalid"},
            headers={"accept": "application/json"},
        )
        assert invalid.status_code == 403
        page = client.get("/admin/runtime")
        assert 'id="runtime-setting-channel_context_chars"' in page.text
        assert "data-runtime-recent-actions" in page.text
        assert "runtime-config-async.js" in page.text
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM admin_commands").fetchone()[0] == 1


@pytest.fixture
def runtime_browser(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    import socket
    import threading

    import uvicorn

    database = tmp_path / "hina.sqlite3"
    store = Store.create(str(database))
    initialize_connection(store.db, ("admin",))
    RuntimeSettings(Settings(discord_token="test", channel_context_chars=6000), store).capture_startup_snapshot()
    store.close()
    app = create_app(DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=True,
    ))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            with playwright.sync_playwright() as driver:
                browser = driver.chromium.launch()
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/admin/runtime")
                yield page, database
                assert errors == []
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)


def test_browser_runtime_save_reset_and_failure_without_navigation(runtime_browser):
    page, database = runtime_browser
    from urllib.parse import parse_qs

    from playwright.sync_api import expect

    def submit(route):
        response = route.fetch()
        command_id = response.json()["command_id"]
        form = parse_qs(route.request.post_data or "")
        store = Store.open_existing(str(database))
        try:
            runtime = RuntimeSettings(Settings(discord_token="test", channel_context_chars=6000), store)
            if form.get("value") == ["4400"]:
                store.db.execute(
                    "UPDATE admin_commands SET status='failed',error_message='fixture rejection' WHERE id=?",
                    (command_id,),
                )
            else:
                if route.request.url.endswith("/reset"):
                    runtime.reset(form["key"][0])
                else:
                    runtime.set_text(form["key"][0], form["value"][0])
                store.db.execute("UPDATE admin_commands SET status='succeeded' WHERE id=?", (command_id,))
            store.db.commit()
        finally:
            store.close()
        route.fulfill(response=response)

    page.route("**/admin/runtime/set", submit)
    page.route("**/admin/runtime/reset", submit)
    page.evaluate("window.runtimeTestSentinel = 123")
    search = page.locator("[data-runtime-config-search-input]")
    search.fill("chars")
    first = page.locator("#runtime-setting-channel_context_chars")
    other = page.locator("#runtime-setting-history_max_chars")
    other.locator("[data-runtime-config-edit]").click()
    other.locator("[data-runtime-config-input]").fill("7777")
    first.locator("[data-runtime-config-edit]").click()
    first.locator("[data-runtime-config-input]").fill("4400")
    first.locator('[data-runtime-config-action-edit] button[type="submit"]').click()
    expect(first.locator("[data-runtime-command-status]")).to_contain_text("fixture rejection")
    assert first.locator("[data-runtime-config-input]").input_value() == "4400"
    first.locator("[data-runtime-config-input]").fill("4300")
    first.locator('[data-runtime-config-action-edit] button[type="submit"]').click()
    expect(first.locator("[data-runtime-command-status]")).to_contain_text("Applied")
    assert first.locator(".runtime-config-current-value > code").inner_text() == "4300"
    assert other.locator("[data-runtime-config-input]").input_value() == "7777"
    assert search.input_value() == "chars"
    assert page.evaluate("window.runtimeTestSentinel") == 123
    first.locator(".runtime-config-reset button").click()
    expect(first.locator("[data-runtime-command-status]")).to_contain_text("Applied")
    assert first.locator(".runtime-config-current-value > code").inner_text() == "6000"
    assert other.locator("[data-runtime-config-input]").input_value() == "7777"
    assert page.evaluate("window.runtimeTestSentinel") == 123
