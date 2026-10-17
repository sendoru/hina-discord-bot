import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.discord.admin_commands import run_admin_command_once


@pytest.fixture
def bulk_app(tmp_path, request):
    database = tmp_path / "hina.sqlite3"
    store = Store(str(database))
    ids = [
        store.add_memory_item(
            Scope(1, 10, 100, True), f"memory {i}", kind="fact", disclosure="local"
        )
        for i in range(4)
    ]
    store.retract_memory_item(ids[2], expected_revision=0)
    store.db.execute("UPDATE memory_items SET status='superseded' WHERE id=?", (ids[3],))
    store.db.commit()
    store.close()
    settings = DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=getattr(request, "param", True),
    )
    return create_app(settings), database, ids


def submission(app, ids, **overrides):
    return {
        "_csrf": app.state.admin_writer.csrf_token,
        "_request_id": "a" * 32,
        "status": "retracted",
        "confirm": "yes",
        "items": json.dumps([{"item_id": i, "expected_revision": 0} for i in ids]),
        **overrides,
    }


def test_bulk_partial_validation_and_idempotent_retry(bulk_app):
    app, database, ids = bulk_app
    with TestClient(app) as client:
        form = submission(app, [*ids, 999])
        first = client.post("/memory/bulk-status", data=form)
        assert first.status_code == 200
        results = first.json()["results"]
        assert [row["status"] for row in results] == [
            "pending",
            "pending",
            "failed",
            "failed",
            "failed",
        ]
        assert "active" in results[2]["error"]
        assert "not found" in results[4]["error"]
        assert client.post("/memory/bulk-status", data=form).json() == first.json()
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT action,target,payload_json FROM admin_commands").fetchall()
        assert len(rows) == 2
        assert all(row[0] == "memory.item.retract" for row in rows)
        assert json.loads(rows[0][2]) == {"item_id": ids[0], "expected_revision": 0}
        assert (
            db.execute("SELECT status FROM memory_items WHERE id=?", (ids[0],)).fetchone()[0]
            == "active"
        )


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"_csrf": ""}, 403),
        ({"confirm": ""}, 422),
        ({"_request_id": "bad"}, 422),
        ({"status": "active"}, 422),
        ({"status": "superseded"}, 422),
        ({"items": "broken"}, 422),
        ({"items": "{}"}, 422),
        ({"items": "[]"}, 422),
        ({"items": json.dumps([{}] * 51)}, 422),
    ],
)
def test_bulk_rejects_invalid_submission_without_writes(bulk_app, overrides, code):
    app, database, ids = bulk_app
    with TestClient(app) as client:
        assert (
            client.post(
                "/memory/bulk-status", data=submission(app, ids[:1], **overrides)
            ).status_code
            == code
        )
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM admin_commands").fetchone()[0] == 0


def test_bulk_read_only_and_selection_ui(bulk_app):
    app, database, ids = bulk_app
    with TestClient(app) as client:
        text = client.get("/memory?status=active&page=1&q=memory").text
        assert "data-memory-select-all" in text
        assert "memory-bulk.js" in text
        assert text.count('data-memory-select="1"') == 2  # desktop + mobile
        assert text.count('data-memory-select="3"') == 0  # filter preserved
        assert "status=active" in text
        assert client.get(f"/memory/{ids[0]}").status_code == 200
    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(database.parent / "usage.jsonl"),
            event_log_path=str(database.parent / "events.jsonl"),
            write_enabled=False,
        )
    )
    with TestClient(app) as client:
        assert "data-memory-bulk-form" not in client.get("/memory").text
        assert "data-memory-select=" not in client.get("/memory").text
        assert client.post("/memory/bulk-status", data=submission(app, ids[:1])).status_code == 403


def test_bulk_reports_invalid_rows_individually(bulk_app):
    app, _, ids = bulk_app
    items = [
        None,
        {"item_id": True, "expected_revision": 0},
        {"item_id": ids[0], "expected_revision": -1},
        {"item_id": ids[1], "expected_revision": 99},
        {"item_id": ids[2], "expected_revision": "0"},
        {"item_id": ids[1], "expected_revision": 0},
    ]
    with TestClient(app) as client:
        results = client.post(
            "/memory/bulk-status", data=submission(app, [], items=json.dumps(items))
        ).json()["results"]
    assert len(results) == 6
    assert all(row["status"] == "failed" for row in results)
    assert results[-1]["error"] == "duplicate memory item"


@pytest.mark.asyncio
async def test_bulk_runtime_partial_failure_and_completed_retry(bulk_app):
    app, database, ids = bulk_app
    form = submission(app, ids[:2])
    with TestClient(app) as client:
        results = client.post("/memory/bulk-status", data=form).json()["results"]
    store = Store(str(database))
    # Change the first row after enqueue: the runtime must reject it and still apply the second.
    store.revise_memory_item(
        ids[0],
        expected_revision=0,
        content="concurrent memory edit",
        kind="fact",
        disclosure="local",
        confidence=1,
    )
    lock = asyncio.Lock()
    runtime = SimpleNamespace(
        store=store, memory_lock=lambda _: lock, events=SimpleNamespace(emit=Mock())
    )
    try:
        assert await run_admin_command_once(runtime)
        assert await run_admin_command_once(runtime)
        assert not await run_admin_command_once(runtime)
        assert store.memory_item(ids[0]).status.value == "active"
        assert store.memory_item(ids[1]).status.value == "retracted"
        assert store.memory_item(ids[1]).revision == 1
        with TestClient(app) as client:
            statuses = [
                client.get(f"/admin/commands/{row['command_id']}").json() for row in results
            ]
            assert [row["status"] for row in statuses] == ["failed", "succeeded"]
            assert "changed" in statuses[0]["error_message"]
            assert client.post("/memory/bulk-status", data=form).json()["results"] == results
    finally:
        store.close()


@pytest.fixture
def memory_browser(bulk_app):
    # Optional browser suite: install .[dashboard-test] and playwright's Chromium.
    playwright = pytest.importorskip("playwright.sync_api")
    import socket
    import threading

    import uvicorn

    app, database, ids = bulk_app
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
                page.goto(f"http://127.0.0.1:{port}/memory")
                yield page, database, ids
                assert errors == []
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)


def test_browser_page_selection_mobile_and_clear(memory_browser):
    page, _, ids = memory_browser
    first = page.locator(f'.desktop-result-list [data-memory-select="{ids[0]}"]')
    second = page.locator(f'.desktop-result-list [data-memory-select="{ids[1]}"]')
    first.check()
    assert page.locator("[data-memory-select-all]").evaluate("(el) => el.indeterminate")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator(f'.mobile-result-list [data-memory-select="{ids[0]}"]').is_checked()
    page.locator("[data-memory-select-all]").check()
    assert page.locator("[data-memory-selection-count]").inner_text() == "2개 선택"
    assert page.locator(f'[data-memory-select="{ids[2]}"]').first.is_disabled()
    page.locator("[data-memory-clear]").click()
    assert page.locator("[data-memory-apply]").is_disabled()
    page.set_viewport_size({"width": 1280, "height": 800})
    second.check()
    # Cancelling the destructive confirmation does not submit or clear selection.
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("[data-memory-apply]").click()
    assert second.is_checked()
    assert page.locator("[data-memory-bulk-feedback]").inner_text() == ""
    page.reload()
    assert page.locator("[data-memory-selection-count]").inner_text() == "0개 선택"


def test_browser_partial_failure_refresh_and_filter_preservation(memory_browser):
    from concurrent.futures import ThreadPoolExecutor

    page, database, ids = memory_browser
    page.goto(page.url + "?status=active&q=memory&page=1")
    page.locator("[data-memory-select-all]").check()

    def apply_commands():
        store = Store(str(database))
        store.revise_memory_item(
            ids[0],
            expected_revision=0,
            content="concurrent memory edit",
            kind="fact",
            disclosure="local",
            confidence=1,
        )

        async def run():
            runtime = SimpleNamespace(
                store=store,
                memory_lock=lambda _: asyncio.Lock(),
                events=SimpleNamespace(emit=Mock()),
            )
            while await run_admin_command_once(runtime):
                pass

        try:
            asyncio.run(run())
        finally:
            store.close()

    def submit(route):
        response = route.fetch()
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(apply_commands).result(timeout=10)
        route.fulfill(response=response)

    page.route("**/memory/bulk-status", submit)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("[data-memory-apply]").click()
    page.wait_for_function(
        "document.querySelector('[data-memory-selection-count]').textContent === '0개 선택'"
    )
    feedback = page.locator("[data-memory-bulk-feedback]").inner_text()
    assert "성공 1개 · 실패 1개 · 처리 중 0개" in feedback
    assert "changed" in feedback
    assert "?status=active&q=memory&page=1" in page.url
    assert page.locator(f'[data-memory-select="{ids[1]}"]').count() == 0
    assert page.locator(f'[data-memory-select="{ids[0]}"]').count() == 2
    assert page.locator("[data-memory-heading]").inner_text().endswith("lifecycle-enabled")
    assert "1개" in page.locator("[data-memory-heading]").inner_text()
    assert page.locator("[data-memory-apply]").is_disabled()


def test_browser_select_all_is_limited_to_current_page(memory_browser):
    page, database, _ = memory_browser
    store = Store(str(database))
    for i in range(51):
        store.add_memory_item(
            Scope(1, 10, 100, True), f"extra memory {i}", kind="fact", disclosure="local"
        )
    store.close()
    page.goto(page.url + "?status=active&page=1")
    page.locator("[data-memory-select-all]").check()
    assert page.locator("[data-memory-selection-count]").inner_text() == "50개 선택"
    page.locator("[data-memory-pagination]").get_by_text("Next").click()
    assert page.locator("[data-memory-selection-count]").inner_text() == "0개 선택"
    page.locator("[data-memory-select-all]").check()
    assert page.locator("[data-memory-selection-count]").inner_text() == "3개 선택"


def test_browser_pending_commands_can_be_checked_again(memory_browser):
    page, database, ids = memory_browser
    page.clock.install()
    page.locator("[data-memory-select-all]").check()
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response("**/memory/bulk-status"):
        page.locator("[data-memory-apply]").click()
    page.wait_for_function(
        "document.querySelector('[data-memory-bulk-feedback]').textContent.includes('pending')"
    )
    page.clock.fast_forward(21_000)
    retry = page.get_by_role("button", name="결과 다시 조회")
    retry.wait_for()
    assert page.locator("[data-memory-apply]").is_disabled()
    assert page.locator("[data-memory-select-all]").is_disabled()
    assert page.locator("[data-memory-results]").evaluate("(el) => el.inert")
    # Supply completed queue state; runtime application is covered by the partial-failure test.
    store = Store(str(database))
    for item_id in ids[:2]:
        store.retract_memory_item(item_id, expected_revision=0)
    store.close()
    with sqlite3.connect(database) as db:
        db.execute("UPDATE admin_commands SET status='succeeded'")
    retry.click()
    page.wait_for_function(
        "document.querySelector('[data-memory-selection-count]').textContent === '0개 선택'"
    )
    assert (
        "성공 2개 · 실패 0개 · 처리 중 0개"
        in page.locator("[data-memory-bulk-feedback]").inner_text()
    )
    assert not page.locator("[data-memory-results]").evaluate("(el) => el.inert")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM admin_commands").fetchone()[0] == 2


def test_legacy_memory_schema_hides_bulk_controls(bulk_app):
    app, database, ids = bulk_app
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE memory_items DROP COLUMN revision")
    with TestClient(app) as client:
        assert "data-memory-bulk-form" not in client.get("/memory").text
        result = client.post("/memory/bulk-status", data=submission(app, ids[:1])).json()[
            "results"
        ][0]
        assert result["status"] == "failed"
        assert "revisions" in result["error"]


@pytest.mark.parametrize("bulk_app", [True, False], indirect=True)
@pytest.mark.parametrize("width", [1440, 768])
def test_browser_memory_column_layout_with_long_identifiers(memory_browser, width):
    page, database, _ = memory_browser
    with sqlite3.connect(database) as db:
        db.execute(
            """UPDATE memory_items SET user_id=?,user_name=?,origin_realm=?,
               origin_channel_id=?,kind=?,disclosure=?,content=?""",
            (
                "221977862859653120",
                "A long dashboard user name 사용자 이름",
                "guild:1489432315523502251",
                "1554321651473195059",
                "relationship",
                "reference_gated",
                "A long memory preview with 한국어 content. " * 30,
            ),
        )
    page.set_viewport_size({"width": width, "height": 1000})
    page.reload()
    table = page.locator(".memory-table")
    columns = table.locator("thead th").evaluate_all(
        "els => Object.fromEntries(els.map(el => [el.textContent.trim(), el.getBoundingClientRect().width]))"
    )
    assert columns["User"] >= 190
    assert columns["Origin"] >= 250
    assert columns["Updated"] >= 170
    assert columns["ID"] < 100
    assert columns["Disclosure"] < 190
    if "선택" in columns:
        assert columns["선택"] <= 60
    # Every preview row must span the complete header grid, including selection.
    assert table.locator("tbody tr").evaluate_all(
        "rows => rows.every(row => [...row.cells].reduce((sum, cell) => sum + cell.colSpan, 0) === row.closest('table').tHead.rows[0].cells.length)"
    )
    # Wide data stays in its scroll region instead of stretching the page.
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    wrap = page.locator(".desktop-result-list")
    assert wrap.evaluate("el => el.scrollWidth > el.clientWidth")
    wrap.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
    assert table.get_by_role("columnheader", name="Updated", exact=True).is_visible()
