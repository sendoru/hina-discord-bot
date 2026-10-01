"""Optional Playwright review using synthetic data and artifacts under /tmp.

Run: python tests/dashboard/check_browser_ui.py --browser /usr/bin/chromium
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from pathlib import Path
from tempfile import mkdtemp

import uvicorn
from playwright.sync_api import sync_playwright
from test_app import dashboard_client

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def build_fixture(root: Path):
    app = dashboard_client(root).app
    store = Store(str(root / "hina.sqlite3"))
    scope = Scope(None, 10, 100)
    content = "긴 한국어 본문과 식별자의 줄바꿈·모바일 탐색을 확인합니다. " * 35
    for index in range(55):
        token = CURRENT_TURN_ID.set(f"browser-trace-{index}")
        try:
            store.add(scope, 1000 + index, content, "합성 응답입니다. " * 30,
                      name="화면 검증 사용자")
        finally:
            CURRENT_TURN_ID.reset(token)
        store.add_memory_item(scope, content, kind="fact", disclosure="local", confidence=0.7)
    with store.db:
        store.db.execute("UPDATE memory_items SET content=? WHERE id IN (1,2)", (content,))
    store.close()
    long_id = "long-trace-" + "0123456789abcdef" * 12
    timestamp = "2026-09-22T00:00:00+00:00"
    streams = {
        "events.jsonl": [
            {"at": timestamp, "turn_id": "trace-ui", "event": "turn.completed",
             "status": "completed", "memory_failures": 2},
            {"at": timestamp, "event": "identity.resolution", "outcome": "resolved",
             "reference_group": "abcdef0123456789" * 4,
             "resolved_user_group": "0123456789abcdef" * 4},
        ],
        "discord-usage.jsonl": [
            {"at": timestamp, "turn_id": "partial-data", "calls": 0},
        ],
        "usage.jsonl": [
            {"at": timestamp, "turn_id": long_id, "operation": "answer", "total_tokens": 20},
        ],
    }
    for name, rows in streams.items():
        with (root / name).open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return app, long_id


def review(base_url: str, root: Path, long_id: str, browser_path: str | None):
    paths = [
        "/", "/traces?memory_failure=yes", "/memory?origin_scope_type=dm", "/reconciliation",
        "/traces/trace-ui", "/identity", "/analytics", "/memory?confidence_min=2",
        "/traces?after=bad-date", "/memory?q=no-results", "/memory/999999", "/traces?page=bad",
        "/conversations?q=한국어&page=2", "/summaries", "/memory/cursors", "/state",
        "/relationships", "/traces/" + long_id,
    ]
    screenshots = root / "screenshots"
    screenshots.mkdir()
    results = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=browser_path, args=["--no-sandbox"])
        try:
            for width in (1440, 768, 390):
                page = browser.new_page(viewport={"width": width, "height": 1000},
                                        is_mobile=width == 390, has_touch=width == 390)
                page.on("pageerror", lambda error: errors.append(str(error)))
                for index, path in enumerate(paths):
                    response = page.goto(base_url + path)
                    expected_status = 404 if path == "/memory/999999" else (
                        422 if path == "/traces?page=bad" else 200)
                    assert response.status == expected_status, (path, response.status)
                    page.screenshot(path=str(screenshots / f"{width}-{index:02d}.png"), full_page=True)
                    overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
                    assert not overflow, (width, path, "viewport overflow")
                    unlabeled = page.locator("input:not([type=hidden]), select").evaluate_all(
                        "els => els.filter(e => !e.labels?.length).map(e => e.name)")
                    assert not unlabeled, (path, unlabeled)
                    results.append({"width": width, "path": path, "status": response.status})
                page.goto(base_url + "/memory?confidence_min=2")
                assert page.locator("[name=confidence_min]").input_value() == "2"
                assert page.locator("[name=confidence_min]").get_attribute("aria-invalid") == "true"
                page.goto(base_url + "/traces?after=2026-09-21T00%3A00%3A12.345678Z")
                assert page.locator("[name=after]").input_value() == "2026-09-21T09:00:12.345678"
                page.goto(base_url + "/memory?origin_scope_type=dm")
                assert page.locator(".mobile-result-list").is_visible() == (width == 390)
                assert page.locator(".desktop-result-list").is_visible() == (width != 390)
                if width == 390:
                    page.locator(".mobile-result-card > details > summary").first.click()
                    assert page.locator(".mobile-result-card > details").first.get_attribute("open") is not None
                    page.locator(".mobile-nav > summary").click()
                    page.locator(".mobile-nav-panel a[href='/traces']").click()
                    assert page.url.endswith("/traces")
                page.close()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(base_url + "/memory?origin_scope_type=dm&page=2")
            page.keyboard.press("Tab")
            assert page.locator(":focus").inner_text() == "본문 바로가기"
            page.keyboard.press("Enter")
            assert page.locator(":focus").get_attribute("id") == "main-content"
            page.locator(".desktop-result-list a").first.click()
            page.locator("a.back").click()
            assert "page=2" in page.url and "origin_scope_type=dm" in page.url
            page.goto(base_url + "/memory?origin_scope_type=guild&origin_guild_id=1&origin_channel_id=20&page=2")
            page.locator(".filter-chip[title='Remove origin_scope_type filter']").click()
            assert "origin_" not in page.url and "page=" not in page.url
            page.goto(base_url + "/memory")
            page.locator(".advanced-filters > summary").click()
            page.locator(".scope-picker input[value=any]").focus()
            page.keyboard.press("ArrowRight")
            assert page.locator(".scope-picker input[value=guild]").is_checked()
            assert page.locator("[name=origin_guild_id]").is_enabled()
            page.keyboard.press("ArrowRight")
            assert page.locator(".scope-picker input[value=dm]").is_checked()
            assert page.locator("[name=origin_guild_id]").is_disabled()
            page.goto(base_url + "/identity")
            page.locator(".identifier-disclosure summary").first.focus()
            page.keyboard.press("Enter")
            assert page.locator(".identifier-disclosure").first.get_attribute("open") is not None
            page.goto(base_url + "/traces/trace-ui")
            for link in page.locator("[aria-label='Trace sections'] a").all():
                target = link.get_attribute("href")
                assert page.locator(target).count() == 1
            page.set_viewport_size({"width": 390, "height": 1000})
            page.goto(base_url + "/analytics")
            scrollable = next(region for region in page.locator(".table-wrap").all()
                              if region.evaluate("el => el.scrollWidth > el.clientWidth"))
            scrollable.focus()
            page.keyboard.press("ArrowRight")
            page.wait_for_function("el => el.scrollLeft > 0", arg=scrollable.element_handle())
            assert not errors, errors
        finally:
            browser.close()
    (root / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(results)} screens passed; artifacts: {root}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Chromium executable; omit to use Playwright's installed browser")
    args = parser.parse_args()
    root = Path(mkdtemp(prefix="hina-dashboard-browser-", dir="/tmp"))
    app, long_id = build_fixture(root)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Fixture server did not start")
                time.sleep(0.05)
            review(f"http://127.0.0.1:{port}", root, long_id, args.browser)
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
