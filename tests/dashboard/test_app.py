import json

from fastapi.testclient import TestClient

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.app import _static_asset_version, create_app
from hina_bot.dashboard.config import DashboardSettings


def test_static_asset_version_tracks_current_file_contents(tmp_path):
    asset = tmp_path / "dashboard.css"
    asset.write_text("a", encoding="utf-8")
    first = _static_asset_version(tmp_path, "dashboard.css")

    asset.write_text("b", encoding="utf-8")
    second = _static_asset_version(tmp_path, "dashboard.css")

    assert len(first) == 12
    assert len(second) == 12
    assert first != second


def write_rows(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def dashboard_client(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store(str(database))
    scope = Scope(None, 10, 100)
    token = CURRENT_TURN_ID.set("trace-ui")
    try:
        store.add(
            scope,
            55,
            "hello dashboard",
            "hello",
            name="Dashboard User",
            context_provenance={
                "version": 1,
                "scope": "dm",
                "current_user_id": "100",
                "egress_policy": "full",
                "decisions": {
                    "use_memory": True,
                    "current_channel_only": False,
                    "cross_channel_memory": True,
                },
                "egress": {
                    "adapter": {},
                    "provider_boundary": {},
                },
                "sections": [{
                    "name": "conversation_history",
                    "included": False,
                    "count": 0,
                    "blocked_count": 0,
                }],
                "sources": [],
                "structured_memory": [],
                "relationship_axes": [],
                "truncated": {"sources": 0, "structured_memory": 0},
            },
        )
    finally:
        CURRENT_TURN_ID.reset(token)

    failed_token = CURRENT_TURN_ID.set("trace-failed-ui")
    try:
        store.add_failed_turn(
            scope,
            56,
            "failed dashboard request",
            name="Dashboard User",
            reply="fallback dashboard reply",
            status="generation_failed",
            stage="generation",
            reply_delivered=True,
            error_type="RuntimeError",
            error_fingerprint="feedface",
        )
    finally:
        CURRENT_TURN_ID.reset(failed_token)

    target_id = store.add_memory_item(
        scope,
        "dashboard memory",
        kind="fact",
        disclosure="reference_gated",
        source_message_ids=("55",),
        confidence=0.95,
    )
    new_id = store.add_memory_item(
        scope,
        "dashboard memory updated",
        kind="fact",
        disclosure="reference_gated",
        source_message_ids=("55",),
        confidence=0.97,
    )
    store.add_memory_reconciliation_proposal(
        scope,
        new_memory_item_id=new_id,
        target_memory_item_id=target_id,
        relation="corrects",
        confidence=0.9,
        source_message_ids=("55",),
    )
    store.add_memory_item(
        scope,
        "comfortable recurring interaction",
        kind="relationship",
        disclosure="implicit",
        source_message_ids=("55",),
        confidence=0.95,
        relationship_evidence={"comfort": 3, "familiarity": 2},
        user_name="Dashboard User",
    )
    guild_scope = Scope(1, 20, 100, True)
    store.observe_guild_channel(1, "Dashboard Guild", 10, "general")
    store.observe_guild_channel(1, "Dashboard Guild", 20, "relationships")
    store.add_shared_call(guild_scope, 57, "Dashboard User", "public dashboard call")
    shared_through = int(store.db.execute(
        "SELECT id FROM shared_calls WHERE message_id='57'"
    ).fetchone()["id"])
    store.save_shared_summary(
        guild_scope,
        "Dashboard User",
        "shared dashboard summary",
        shared_through,
    )
    store.add_memory_item(
        guild_scope,
        "same guild raw relationship",
        kind="relationship",
        disclosure="local",
        source_message_ids=("55",),
        confidence=0.88,
        relationship_evidence={"casualness": 2},
        user_name="Dashboard User",
    )
    store.set_memory_mode_override("global", "read_only")
    store.set_memory_mode_override(guild_scope.realm, "normal")
    store.set_chat_log_mode_override("global", "on")
    store.set_note("config:chatlog_capture:global", "direct")
    store.set_note(guild_scope.realm, "dashboard server note")
    store.set_note(guild_scope.user_note, "dashboard user note")
    with store.db:
        store.db.execute(
            """CREATE TABLE observability_epochs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   reset_at TEXT NOT NULL
               )"""
        )
        store.db.execute(
            "INSERT INTO observability_epochs(reset_at) VALUES (?)",
            ("2026-09-20T00:00:00+00:00",),
        )
    store.close()

    usage = tmp_path / "usage.jsonl"
    exchange = tmp_path / "discord-usage.jsonl"
    events = tmp_path / "events.jsonl"
    write_rows(
        usage,
        [
            {
                "at": "2026-09-21T00:00:00.500000+00:00",
                "turn_id": "trace-ui",
                "operation": "model_route_classify",
                "model": "classifier-model",
                "status": "error",
                "error_type": "CancelledError",
                "elapsed_ms": 4001,
            },
            {
                "at": "2026-09-21T00:00:01+00:00",
                "turn_id": "trace-ui",
                "operation": "answer",
                "model": "test-model",
                "model_tier": "fast",
                "total_tokens": 12,
                "status": "completed",
            }
        ],
    )
    write_rows(
        exchange,
        [
            {
                "at": "2026-09-21T00:00:01+00:00",
                "turn_id": "trace-ui",
                "scope": "dm",
                "status": "completed_with_api_errors",
                "models": ["classifier-model", "test-model"],
                "calls": 2,
                "failed_calls": 1,
                "total_tokens": 12,
                "web_search_calls": 0,
            }
        ],
    )
    write_rows(
        events,
        [
            {
                "at": "2026-09-21T00:00:00+00:00",
                "turn_id": "trace-ui",
                "event": "turn.received",
                "scope": "dm",
            },
            {
                "at": "2026-09-21T00:00:02+00:00",
                "turn_id": "trace-ui",
                "event": "turn.completed",
                "scope": "dm",
                "status": "completed",
                "elapsed_ms": 100,
            },
            {
                "at": "2026-09-21T00:00:03+00:00",
                "turn_id": "trace-failed-ui",
                "event": "turn.received",
                "scope": "dm",
            },
            {
                "at": "2026-09-21T00:00:04+00:00",
                "turn_id": "trace-failed-ui",
                "event": "turn.failed",
                "scope": "dm",
                "status": "generation_failed",
                "stage": "generation",
                "reply_delivered": True,
                "raw_turn_persistence": "stored",
                "error_type": "RuntimeError",
                "error_fingerprint": "feedface",
                "elapsed_ms": 200,
            },
        ],
    )

    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(usage),
            event_log_path=str(events),
        )
    )
    return TestClient(app)


def test_dashboard_health_uses_read_only_sources(tmp_path):
    client = dashboard_client(tmp_path)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "telemetry_sources": {
            "usage_files": 1,
            "exchange_files": 1,
            "event_files": 1,
        },
    }


def test_dashboard_read_only_pages_render(tmp_path):
    client = dashboard_client(tmp_path)

    overview = client.get("/")
    traces = client.get("/traces?tier=fast")
    analytics = client.get("/analytics?operation=answer")
    detail = client.get("/traces/trace-ui")
    failed_detail = client.get("/traces/trace-failed-ui")
    conversations = client.get("/conversations?q=dashboard")
    conversations_by_name = client.get("/conversations?user_id=Dashboard%20User")
    conversation_context = client.get("/conversations/1/context")
    memory = client.get("/memory?q=dashboard")
    memory_dm = client.get("/memory?origin_scope_type=dm")
    memory_legacy_dm = client.get(
        "/memory?origin_realm=dm:100&origin_channel_id=10"
    )
    memory_by_name = client.get("/memory?user_id=Dashboard%20User")
    memory_detail = client.get("/memory/1")
    relationships = client.get(
        "/relationships?target_guild_id=1&target_channel_id=10&q=Dashboard"
    )
    relationships_dm = client.get(
        "/relationships?target_scope_type=dm&q=Dashboard"
    )
    state = client.get(
        "/state?target_guild_id=1&target_channel_id=20&target_user_id=100&q=dashboard"
    )
    state_dm = client.get(
        "/state?target_scope_type=dm&target_user_id=100"
    )
    summaries = client.get("/summaries?q=dashboard")
    cursors = client.get("/memory/cursors?user_id=100")
    reconciliation = client.get("/reconciliation?relation=corrects")
    reconciliation_dm = client.get("/reconciliation?origin_scope_type=dm")
    reconciliation_detail = client.get("/reconciliation/1")
    static = client.get("/static/dashboard.css")
    scope_script = client.get("/static/scope-picker.js")
    clipboard_script = client.get("/static/clipboard.js")
    icon = client.get("/static/hina-dashboard-icon.webp")

    assert overview.status_code == 200
    assert "Hina Dashboard" in overview.text
    assert 'aria-label="Dashboard sections"' in overview.text
    assert 'class="mobile-nav"' in overview.text
    assert 'aria-label="Mobile dashboard sections"' in overview.text
    assert "Navigate" in overview.text
    assert 'class="nav-link nav-home active"' in overview.text
    assert 'hina-dashboard-icon.webp' in overview.text
    assert 'rel="icon" type="image/webp"' in overview.text
    assert '/static/dashboard.css?v=' in overview.text
    assert '/static/clipboard.js?v=' in overview.text
    assert "20261006-trace-issues" not in overview.text
    assert "20261003-typed-runtime" not in overview.text
    assert "Observability" in overview.text
    assert "Context" in overview.text
    assert "Memory ops" in overview.text
    assert 'href="/relationships"' in overview.text
    assert 'href="/state"' in overview.text
    assert "viewport-fit=cover" in overview.text
    assert "Asia/Seoul" in overview.text
    assert "2026-09-21 09:00:00 KST" in overview.text
    assert 'class="freshness-badge freshness-danger"' in overview.text
    assert "마지막 기록" in overview.text
    assert 'class="status-badge status-warning">completed · degraded</span>' in overview.text
    assert 'href="/traces" aria-label="View all traces"' in overview.text
    assert 'href="/conversations" aria-label="View stored conversations"' in overview.text
    assert 'href="/analytics#usage-breakdown" aria-label="View API call breakdown"' in overview.text
    assert 'href="/analytics#web-search-routing" aria-label="View web-search analytics"' in overview.text
    assert "Current observability epoch #1" in overview.text
    assert "trace-ui" in traces.text
    assert "Current observability epoch #1" in traces.text
    assert 'aria-label="Observability epoch"' in traces.text
    assert "Advanced filters" in traces.text
    assert "Final failure" in traces.text
    assert "Issue" in traces.text
    assert "<th>Issue</th>" not in traces.text
    assert "model_route_classify · CancelledError" not in traces.text
    assert 'class="status-badge status-warning">completed · degraded</span>' in traces.text
    assert 'class="issue-count-badge"' in traces.text
    assert "⚠ 1" in traces.text
    assert 'class="wide-table"' in traces.text
    assert 'aria-label="Applied filters"' in traces.text
    assert analytics.status_code == 200
    assert "Routing & Usage Analytics" in analytics.text
    assert "test-model" in analytics.text
    assert "Current observability epoch #1" in analytics.text
    assert 'aria-label="Analytics sections"' in analytics.text
    assert 'href="#runtime-performance"' in analytics.text
    assert 'id="model-routing"' in analytics.text
    assert 'id="web-search-routing"' in analytics.text
    assert 'id="usage-breakdown"' in analytics.text
    assert "hello dashboard" in detail.text
    assert 'data-copy-text="trace-ui"' in detail.text
    assert 'data-copy-text="55"' in detail.text
    assert 'data-copy-block' in detail.text
    assert "Dashboard User" in detail.text
    assert '<code class="scope-id" data-copy-text="100">100</code>' in detail.text
    assert "<dt>Channel</dt>" in detail.text
    assert "Raw scope" in detail.text
    assert "Context &amp; provenance" in detail.text
    assert "Egress policy" in detail.text
    assert "degraded completion" in detail.text
    assert 'id="trace-issues"' in detail.text
    assert "model_route_classify · CancelledError" in detail.text
    assert "api_error" in detail.text
    assert failed_detail.status_code == 200
    assert "Failed conversation record" in failed_detail.text
    assert "failed dashboard request" in failed_detail.text
    assert "fallback dashboard reply" in failed_detail.text
    assert "generation_failed" in failed_detail.text
    assert "Fallback delivered" in failed_detail.text
    assert "feedface" in failed_detail.text
    assert "일반 대화 기록·요약·구조화 메모리에는 포함되지 않습니다." in failed_detail.text
    assert "hello dashboard" in conversations.text
    assert 'data-copy-text="55"' in conversations.text
    assert conversations.text.count("data-copy-block") >= 2
    assert "Content search" in conversations.text
    assert "Advanced filters" in conversations.text
    assert 'class="filter-row grouped-filter-row"' in conversations.text
    assert "<legend>Context</legend>" in conversations.text
    assert 'class="conversation-head-primary"' in conversations.text
    assert 'class="metadata compact conversation-metadata"' in conversations.text
    assert "<dt>Realm</dt>" in conversations.text
    assert "<dt>Channel</dt>" in conversations.text
    assert 'class="scope-key"' not in conversations.text
    assert 'aria-label="Applied filters"' in conversations.text
    assert 'type="datetime-local"' in conversations.text
    assert "<mark>dashboard</mark>" in conversations.text
    assert conversations_by_name.status_code == 200
    assert "hello dashboard" in conversations_by_name.text
    assert conversation_context.status_code == 200
    assert "Conversation Context" in conversation_context.text
    assert "<dt>Realm</dt>" in conversation_context.text
    assert "<dt>Channel</dt>" in conversation_context.text
    assert 'class="scope-key"' not in conversation_context.text
    assert "dashboard memory" in memory.text
    assert "Content search" in memory.text
    assert "Advanced filters" in memory.text
    assert 'aria-label="Applied filters"' in memory.text
    assert 'aria-label="Origin scope type"' in memory.text
    assert 'name="origin_scope_type" value="any" checked' in memory.text
    assert memory_dm.status_code == 200
    assert "dashboard memory" in memory_dm.text
    assert 'name="origin_scope_type" value="dm" checked' in memory_dm.text
    assert memory_legacy_dm.status_code == 200
    assert 'name="origin_scope_type" value="dm" checked' in memory_legacy_dm.text
    assert 'name="origin_channel_id" inputmode="numeric" value="10"' in memory_legacy_dm.text
    assert "<mark>dashboard</mark>" in memory.text
    assert memory_by_name.status_code == 200
    assert "dashboard memory" in memory_by_name.text
    assert "hello dashboard" in memory_detail.text
    assert relationships.status_code == 200
    assert "Effective Relationship Profiles" in relationships.text
    assert "Dashboard User" in relationships.text
    assert "3/4" in relationships.text
    assert "comfortable recurring interaction" in relationships.text
    assert "FULL/raw relationship memory" in relationships.text
    assert "same guild raw relationship" in relationships.text
    assert "Dashboard Guild" in relationships.text
    assert "#relationships" in relationships.text
    assert "Dashboard Guild" in relationships.text
    assert "#relationships" in relationships.text
    assert "Cross-space IMPLICIT projection" in relationships.text
    assert 'class="table-wrap relationship-desktop"' in relationships.text
    assert 'class="relationship-mobile"' in relationships.text
    assert 'class="relationship-mobile-card"' in relationships.text
    assert 'aria-label="Target scope type"' in relationships.text
    assert 'name="target_scope_type" value="guild" checked' in relationships.text
    assert relationships_dm.status_code == 200
    assert 'name="target_scope_type" value="dm" checked' in relationships_dm.text
    assert 'data-dm-channel="false"' in relationships_dm.text
    assert 'name="target_guild_id" inputmode="numeric" value=""' in relationships_dm.text
    assert 'name="target_channel_id" inputmode="numeric" value=""' in relationships_dm.text
    assert "Target:" in relationships_dm.text
    assert "<code>dm</code>" in relationships_dm.text
    assert "Owner-DM relationship memory" in relationships_dm.text
    assert "structured_owner_memory" in relationships_dm.text
    assert "owner_relationship_profile" in relationships_dm.text
    assert "Owner profile inputs" in relationships_dm.text
    assert "comfortable recurring interaction" in relationships_dm.text
    assert state.status_code == 200
    assert "Memory &amp; Context State" in state.text
    assert 'class="state-query-panel"' in state.text
    assert 'aria-labelledby="state-scope-title"' in state.text
    assert 'aria-labelledby="state-note-title"' in state.text
    assert 'type="search" name="q"' in state.text
    assert '<input type="hidden" name="q" value="dashboard">' in state.text
    assert '<input type="hidden" name="target_guild_id" value="1">' in state.text
    assert '<input type="hidden" name="target_channel_id" value="20">' in state.text
    assert '<input type="hidden" name="target_user_id" value="100">' in state.text
    assert ">Clear</a>" in state.text
    assert 'aria-label="Target scope type"' in state.text
    assert 'name="target_scope_type" value="guild" checked' in state.text
    assert state_dm.status_code == 200
    assert 'name="target_scope_type" value="dm" checked' in state_dm.text
    assert 'data-dm-channel="false"' in state_dm.text
    assert 'name="target_dm_channel_id"' in state_dm.text
    assert '<option value="10" selected>10</option>' in state_dm.text
    assert "유일하게 알려진 DM 채널을 자동 선택했습니다." in state_dm.text
    assert '<span>DM · user</span> <code class="scope-id" data-copy-text="100">100</code>' in state_dm.text
    assert '<code class="scope-raw-key">dm:100:channel:10:user:100</code>' in state_dm.text
    assert '<code class="scope-id" data-copy-text="10">10</code>' in state_dm.text
    assert "dashboard server note" in state.text
    assert '<code class="scope-raw-key">guild:1:user:100</code>' in state.text
    assert "dashboard user note" in state.text
    assert "read_only" in state.text
    assert "direct" in state.text
    assert 'class="wide-table"' not in state.text
    assert "shared dashboard summary" in summaries.text
    assert '<code class="scope-id" data-copy-text="100">100</code>' in summaries.text
    assert "Dashboard User" in summaries.text
    assert "<th>Realm</th><th>Channel</th><th>User</th>" in summaries.text
    assert "Raw scope" not in summaries.text
    assert "Memory Extraction Cursors" in cursors.text
    assert '<code class="scope-id" data-copy-text="100">100</code>' in cursors.text
    assert "Dashboard User" in cursors.text
    assert "<th>Realm</th><th>Channel</th><th>User</th>" in cursors.text
    assert "Raw scope" not in cursors.text
    assert "dashboard memory updated" in reconciliation.text
    assert "Dashboard User" in reconciliation.text
    assert 'aria-label="Origin scope type"' in reconciliation.text
    assert reconciliation_dm.status_code == 200
    assert "dashboard memory updated" in reconciliation_dm.text
    assert 'name="origin_scope_type" value="dm" checked' in reconciliation_dm.text
    assert "Target / old" in reconciliation_detail.text
    assert "Dashboard User · <code>100</code>" in reconciliation_detail.text
    assert "dashboard memory updated" in reconciliation_detail.text
    assert static.status_code == 200
    assert scope_script.status_code == 200
    assert clipboard_script.status_code == 200
    assert "syncScopePicker" in scope_script.text
    assert "navigator.clipboard" in clipboard_script.text
    assert "[data-copy-text]" in clipboard_script.text
    assert "makeBlockCopyable" in clipboard_script.text
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/webp"
    assert icon.content
    assert "color-scheme" in static.text
    assert "@media (max-width: 720px)" in static.text
    assert ".advanced-filters > summary {\n    display: list-item;" in static.text
    assert "@media (max-width: 480px)" in static.text
    assert "Some mobile browsers expose an effective CSS viewport wider than 720px" in static.text
    assert "white-space: nowrap" in static.text
    assert "overscroll-behavior-x: contain" in static.text
    assert ".app-shell" in static.text
    assert ".sidebar-nav" in static.text
    assert ".nav-link.active" in static.text
    assert ".notice.warning" in static.text
    assert ".mobile-nav-panel" in static.text
    assert ".wide-table" in static.text
    assert ".issue-count-badge" in static.text
    assert ".trace-issue-list" in static.text
    assert ".relationship-mobile-card" in static.text
    assert ".relationship-desktop" in static.text
    assert ".scope-picker" in static.text
    assert ".scope-picker-types" in static.text
    assert ".copy-button" in static.text
    assert ".copy-toast" in static.text
    assert "[data-copy-text]" in static.text


def test_unknown_trace_returns_404(tmp_path):
    client = dashboard_client(tmp_path)

    response = client.get("/traces/not-found")

    assert response.status_code == 404


def test_unknown_memory_item_returns_404(tmp_path):
    client = dashboard_client(tmp_path)

    response = client.get("/memory/9999")

    assert response.status_code == 404


def test_unknown_reconciliation_proposal_returns_404(tmp_path):
    client = dashboard_client(tmp_path)

    response = client.get("/reconciliation/9999")

    assert response.status_code == 404


def test_invalid_filters_preserve_input_and_do_not_query(tmp_path, monkeypatch):
    client = dashboard_client(tmp_path)
    def forbidden(**kwargs):
        raise AssertionError("Invalid filters must not reach the repository")
    monkeypatch.setattr(client.app.state.repository, "count_memory_items", forbidden)
    for value in ("2", "bad", "nan", "inf", "-0.1"):
        response = client.get("/memory", params={"confidence_min": value})
        assert response.status_code == 200
        assert f'value="{value}"' in response.text
        assert 'aria-invalid="true"' in response.text
        assert "입력 오류로 조회하지 않았습니다" in response.text
    for path in ("/traces", "/conversations", "/analytics", "/reconciliation"):
        field = "created_after" if path == "/reconciliation" else "after"
        response = client.get(path, params={field: "bad-date"})
        assert response.status_code == 200
        assert 'value="bad-date"' in response.text
        assert "올바른 날짜" in response.text
    response = client.get("/memory?confidence_min=0.9&confidence_max=0.1")
    assert "끝 값은 시작 값 이상" in response.text


def test_detail_links_preserve_list_filters_and_return_paths(tmp_path):
    client = dashboard_client(tmp_path)
    for path, detail in [("/traces", "/traces/trace-ui"), ("/memory", "/memory/1"),
                         ("/reconciliation", "/reconciliation/1"),
                         ("/conversations", "/conversations/1/context")]:
        response = client.get(path, params={"q": "dashboard", "page": 2})
        assert "return_to=" in response.text or path == "/traces"
        response = client.get(detail, params={"return_to": path + "?q=dashboard&page=2"})
        assert f'class="back" href="{path}?q=dashboard&amp;page=2"' in response.text
        response = client.get(detail, params={"return_to": "https://evil.example"})
        assert f'class="back" href="{path}"' in response.text


def test_html_error_pages_preserve_status_and_json_clients(tmp_path):
    client = dashboard_client(tmp_path)
    for url, status in [("/traces/missing", 404), ("/memory/99999", 404),
                        ("/reconciliation/99999", 404), ("/conversations/999/context", 404),
                        ("/traces?page=bad", 422), ("/memory?page=0", 422)]:
        response = client.get(url, headers={"Accept": "text/html"})
        assert response.status_code == status
        assert response.headers["content-type"].startswith("text/html")
        assert "목록으로 돌아가기" in response.text
        response = client.get(url, headers={"Accept": "application/json"})
        assert response.status_code == status
        assert "detail" in response.json()
    assert client.get("/healthz").json()["status"] == "ok"


def test_keyboard_and_filter_accessibility_markup(tmp_path):
    from html.parser import HTMLParser

    class Labels(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.labels = set()
            self.controls = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "label":
                self.depth += 1
                if "for" in attrs:
                    self.labels.add(attrs["for"])
            if tag in {"input", "select"} and attrs.get("type") != "hidden":
                self.controls.append((self.depth, attrs.get("id")))
        def handle_endtag(self, tag):
            if tag == "label":
                self.depth -= 1

    client = dashboard_client(tmp_path)
    for path in ("/traces", "/memory", "/reconciliation", "/analytics",
                 "/conversations", "/summaries", "/memory/cursors", "/state", "/relationships"):
        response = client.get(path)
        parser = Labels()
        parser.feed(response.text)
        assert all(depth or control_id in parser.labels for depth, control_id in parser.controls), path
        assert 'aria-current="page"' in response.text
        assert 'href="#main-content"' in response.text


def test_trace_sections_and_mobile_results_are_available(tmp_path):
    client = dashboard_client(tmp_path)
    response = client.get("/traces/trace-ui")
    for section in ("trace-summary", "stored-turn", "context-provenance", "trace-timeline"):
        assert f'href="#{section}"' in response.text
        assert f'id="{section}"' in response.text
    for path in ("/traces", "/memory", "/reconciliation"):
        response = client.get(path)
        assert 'class="mobile-result-list"' in response.text
        assert 'class="mobile-result-card"' in response.text
        assert "메타데이터" in response.text
