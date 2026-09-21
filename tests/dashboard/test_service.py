import json

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.repository import AdminRepository
from hina_bot.dashboard.service import DashboardService
from hina_bot.dashboard.telemetry import TelemetryReader


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_service(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store(str(database))
    token = CURRENT_TURN_ID.set("trace-1")
    try:
        store.add(Scope(1, 10, 100, True), 55, "question", "answer")
    finally:
        CURRENT_TURN_ID.reset(token)
    store.close()

    usage = tmp_path / "logs" / "usage.jsonl"
    exchange = tmp_path / "logs" / "discord-usage.jsonl"
    events = tmp_path / "logs" / "events.jsonl"
    write_rows(
        usage,
        [
            {
                "at": "2026-09-21T00:00:01+00:00",
                "turn_id": "trace-1",
                "operation": "answer",
                "model": "smart-model",
                "model_tier": "smart",
                "total_tokens": 150,
                "web_search_calls": 1,
                "web_search_used": True,
                "elapsed_ms": 800,
                "status": "completed",
            },
            {
                "at": "2026-09-21T00:01:01+00:00",
                "turn_id": "trace-2",
                "operation": "answer",
                "model": "fast-model",
                "model_tier": "fast",
                "total_tokens": 40,
                "web_search_calls": 0,
                "web_search_used": False,
                "status": "completed",
            },
        ],
    )
    write_rows(
        exchange,
        [
            {
                "at": "2026-09-21T00:00:01+00:00",
                "turn_id": "trace-1",
                "scope": "guild",
                "status": "completed",
                "models": ["smart-model"],
                "calls": 1,
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "cached_tokens": 20,
                "reasoning_tokens": 10,
                "web_search_calls": 1,
                "elapsed_ms": 900,
            },
            {
                "at": "2026-09-21T00:01:01+00:00",
                "turn_id": "trace-2",
                "scope": "dm",
                "status": "completed",
                "models": ["fast-model"],
                "calls": 1,
                "input_tokens": 30,
                "output_tokens": 10,
                "total_tokens": 40,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
                "web_search_calls": 0,
                "elapsed_ms": 400,
            },
        ],
    )
    write_rows(
        events,
        [
            {
                "at": "2026-09-21T00:00:00+00:00",
                "turn_id": "trace-1",
                "event": "turn.received",
                "scope": "guild",
            },
            {
                "at": "2026-09-21T00:00:02+00:00",
                "turn_id": "trace-1",
                "event": "turn.completed",
                "scope": "guild",
                "status": "completed",
                "elapsed_ms": 1000,
                "memory_failures": 1,
            },
            {
                "at": "2026-09-21T00:01:00+00:00",
                "turn_id": "trace-2",
                "event": "turn.received",
                "scope": "dm",
            },
            {
                "at": "2026-09-21T00:01:02+00:00",
                "turn_id": "trace-2",
                "event": "turn.failed",
                "scope": "dm",
                "status": "generation_failed",
                "elapsed_ms": 500,
                "error_fingerprint": "deadbeef",
            },
        ],
    )
    return DashboardService(
        AdminRepository(database),
        TelemetryReader(usage, events),
    )


def test_overview_aggregates_trace_and_exchange_metrics(tmp_path):
    service = build_service(tmp_path)

    data = service.overview()

    assert data["trace_count"] == 2
    assert data["stored_turn_count"] == 1
    assert data["status_counts"] == {"generation_failed": 1, "completed": 1}
    assert data["tier_counts"] == {"fast": 1, "smart": 1}
    assert data["api_calls"] == 2
    assert data["tokens"]["total_tokens"] == 190
    assert data["web_search_calls"] == 1
    assert data["memory_failures"] == 1
    assert data["error_fingerprints"] == [("deadbeef", 1)]


def test_trace_filters_and_pagination_are_server_side(tmp_path):
    service = build_service(tmp_path)

    data = service.traces(
        scope="guild",
        tier="smart",
        operation="answer",
        error="no",
        web_search="yes",
        after="2026-09-20T23:59:00+00:00",
        before="2026-09-21T00:00:30+00:00",
    )

    assert data["page"].total == 1
    assert [row["turn_id"] for row in data["rows"]] == ["trace-1"]
    assert data["rows"][0]["stored"] is True
    assert data["rows"][0]["models"] == ("smart-model",)
    assert data["rows"][0]["operations"] == ("answer",)


def test_trace_detail_correlates_raw_turn_and_timeline(tmp_path):
    service = build_service(tmp_path)

    data = service.trace("trace-1")

    assert data is not None
    assert data["stored"]["content"] == "question"
    assert data["summary"]["status"] == "completed"
    assert [item["source"] for item in data["timeline"]] == [
        "event",
        "usage",
        "exchange",
        "event",
    ]
    assert service.trace("missing") is None


def test_conversations_use_bounded_repository_filters(tmp_path):
    service = build_service(tmp_path)

    data = service.conversations(query="question")

    assert data["page"].total == 1
    assert data["rows"][0]["reply"] == "answer"
    assert service.conversations(query="not-found")["page"].total == 0


def build_memory_service(tmp_path):
    database = tmp_path / "memory.sqlite3"
    store = Store(str(database))
    scope = Scope(1, 10, 100, True)
    token = CURRENT_TURN_ID.set("memory-trace")
    try:
        store.add(scope, 700, "source for memory", "reply")
    finally:
        CURRENT_TURN_ID.reset(token)
    source_turn_id = int(store.db.execute(
        "SELECT id FROM turns WHERE message_id='700'"
    ).fetchone()["id"])
    store.save_summary(scope, "legacy personal summary", source_turn_id)
    item_id = store.add_memory_item(
        scope,
        "remembered fact",
        kind="fact",
        disclosure="reference_gated",
        source_message_ids=("700",),
        confidence=0.91,
    )
    store.close()
    return (
        DashboardService(
            AdminRepository(database),
            TelemetryReader("", ""),
        ),
        item_id,
        scope,
    )


def test_memory_service_filters_and_source_drilldown(tmp_path):
    service, item_id, _ = build_memory_service(tmp_path)

    listing = service.memory_items(
        user_id="100",
        kind="fact",
        disclosure="reference_gated",
        confidence_min="0.9",
        query="remembered",
    )
    assert listing["page"].total == 1
    assert listing["rows"][0]["source_message_ids_decoded"] == ("700",)

    detail = service.memory_item(item_id)
    assert detail is not None
    assert detail["item"]["content"] == "remembered fact"
    assert detail["sources"][0]["turn"]["turn_id"] == "memory-trace"


def test_summary_and_cursor_service_show_rollout_state(tmp_path):
    service, _, scope = build_memory_service(tmp_path)

    summaries = service.summaries(user_id="100")
    assert len(summaries["personal"]) == 1
    assert summaries["personal"][0]["structured_memory_count"] == 1

    cursors = service.extraction_cursors(user_id="100")
    row = next(row for row in cursors["rows"] if row["scope"] == scope.conversation)
    assert row["initialized"] == 0
    assert row["effective_through_id"] == row["summary_through_id"]
    assert row["cursor_delta"] is None
