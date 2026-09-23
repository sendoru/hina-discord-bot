import json

from fastapi.testclient import TestClient

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings


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
    through = int(store.db.execute(
        "SELECT id FROM turns WHERE message_id='55'"
    ).fetchone()["id"])
    store.save_summary(scope, "legacy dashboard summary", through)
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
    guild_scope = Scope(1, 20, 100)
    store.set_memory_mode_override("global", "read_only")
    store.set_memory_mode_override(guild_scope.realm, "normal")
    store.set_chat_log_mode_override("global", "on")
    store.set_note("config:chatlog_capture:global", "direct")
    store.set_note(guild_scope.realm, "dashboard server note")
    store.set_note(guild_scope.user_note, "dashboard user note")
    store.close()

    usage = tmp_path / "usage.jsonl"
    exchange = tmp_path / "discord-usage.jsonl"
    events = tmp_path / "events.jsonl"
    write_rows(
        usage,
        [
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
                "models": ["test-model"],
                "calls": 1,
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
    identity = client.get("/identity")
    detail = client.get("/traces/trace-ui")
    conversations = client.get("/conversations?q=dashboard")
    conversations_by_name = client.get("/conversations?user_id=Dashboard%20User")
    conversation_context = client.get("/conversations/1/context")
    memory = client.get("/memory?q=dashboard")
    memory_by_name = client.get("/memory?user_id=Dashboard%20User")
    memory_detail = client.get("/memory/1")
    relationships = client.get(
        "/relationships?target_guild_id=1&target_channel_id=10&q=Dashboard"
    )
    state = client.get(
        "/state?target_guild_id=1&target_channel_id=20&target_user_id=100&q=dashboard"
    )
    summaries = client.get("/summaries?q=dashboard")
    cursors = client.get("/memory/cursors?user_id=100")
    reconciliation = client.get("/reconciliation?relation=corrects")
    reconciliation_detail = client.get("/reconciliation/1")
    static = client.get("/static/dashboard.css")

    assert overview.status_code == 200
    assert "Hina Dashboard" in overview.text
    assert 'aria-label="Dashboard sections"' in overview.text
    assert 'href="/relationships"' in overview.text
    assert 'href="/state"' in overview.text
    assert "viewport-fit=cover" in overview.text
    assert "Asia/Seoul" in overview.text
    assert "2026-09-21 09:00:00 KST" in overview.text
    assert "trace-ui" in traces.text
    assert "Advanced filters" in traces.text
    assert 'aria-label="Active filters"' in traces.text
    assert analytics.status_code == 200
    assert "Routing & Usage Analytics" in analytics.text
    assert "test-model" in analytics.text
    assert identity.status_code == 200
    assert "Speaker Identity Observability" in identity.text
    assert "hello dashboard" in detail.text
    assert "Context &amp; provenance" in detail.text
    assert "Egress policy" in detail.text
    assert "hello dashboard" in conversations.text
    assert "Content search" in conversations.text
    assert "Advanced filters" in conversations.text
    assert 'aria-label="Active filters"' in conversations.text
    assert 'type="datetime-local"' in conversations.text
    assert "<mark>dashboard</mark>" in conversations.text
    assert conversations_by_name.status_code == 200
    assert "hello dashboard" in conversations_by_name.text
    assert conversation_context.status_code == 200
    assert "Conversation Context" in conversation_context.text
    assert "dashboard memory" in memory.text
    assert "Content search" in memory.text
    assert "Advanced filters" in memory.text
    assert 'aria-label="Active filters"' in memory.text
    assert "<mark>dashboard</mark>" in memory.text
    assert memory_by_name.status_code == 200
    assert "dashboard memory" in memory_by_name.text
    assert "hello dashboard" in memory_detail.text
    assert relationships.status_code == 200
    assert "Effective Relationship Profiles" in relationships.text
    assert "Dashboard User" in relationships.text
    assert "3/4" in relationships.text
    assert "comfortable recurring interaction" in relationships.text
    assert state.status_code == 200
    assert "Memory &amp; Context State" in state.text
    assert "dashboard server note" in state.text
    assert "dashboard user note" in state.text
    assert "read_only" in state.text
    assert "direct" in state.text
    assert "legacy dashboard summary" in summaries.text
    assert "Memory Extraction Cursors" in cursors.text
    assert "dashboard memory updated" in reconciliation.text
    assert "Target / old" in reconciliation_detail.text
    assert "dashboard memory updated" in reconciliation_detail.text
    assert static.status_code == 200
    assert "color-scheme" in static.text
    assert "@media (max-width: 720px)" in static.text
    assert "@media (max-width: 480px)" in static.text
    assert "Some mobile browsers expose an effective CSS viewport wider than 720px" in static.text
    assert "white-space: nowrap" in static.text
    assert "overscroll-behavior-x: contain" in static.text


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
