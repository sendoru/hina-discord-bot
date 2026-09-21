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
