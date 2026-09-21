import json

from hina_bot.dashboard.telemetry import TelemetryReader


def write_rows(path, rows, *, trailing=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows) + trailing,
        encoding="utf-8",
    )


def test_telemetry_reader_reads_rotations_oldest_first_and_correlates_trace(tmp_path):
    usage = tmp_path / "logs" / "usage.jsonl"
    events = tmp_path / "logs" / "events.jsonl"
    exchange = tmp_path / "logs" / "discord-usage.jsonl"

    write_rows(
        usage.with_name("usage.jsonl.2"),
        [{"at": "2026-09-20T01:00:00+00:00", "turn_id": "a", "operation": "answer"}],
    )
    write_rows(
        usage.with_name("usage.jsonl.1"),
        [{"at": "2026-09-20T02:00:00+00:00", "turn_id": "trace", "operation": "route"}],
        trailing="{broken",
    )
    write_rows(
        usage,
        [{"at": "2026-09-20T03:00:00+00:00", "turn_id": "trace", "operation": "answer"}],
    )
    write_rows(
        exchange,
        [{"at": "2026-09-20T03:01:00+00:00", "turn_id": "trace", "calls": 2}],
    )
    write_rows(
        events,
        [{"at": "2026-09-20T03:02:00+00:00", "turn_id": "trace", "event": "turn.completed"}],
    )

    reader = TelemetryReader(usage, events)
    snapshot = reader.snapshot()

    assert [row["operation"] for row in snapshot.usage] == ["answer", "route", "answer"]
    assert snapshot.oldest_at == "2026-09-20T01:00:00+00:00"
    assert snapshot.newest_at == "2026-09-20T03:02:00+00:00"

    trace = reader.for_turn("trace")
    assert len(trace.usage) == 2
    assert len(trace.exchanges) == 1
    assert len(trace.events) == 1
    assert trace.events[0]["event"] == "turn.completed"


def test_telemetry_reader_accepts_disabled_or_missing_sources(tmp_path):
    reader = TelemetryReader("", tmp_path / "missing-events.jsonl")
    snapshot = reader.snapshot()
    assert snapshot.usage == ()
    assert snapshot.exchanges == ()
    assert snapshot.events == ()
    assert snapshot.oldest_at is None
    assert snapshot.newest_at is None
    assert reader.source_status() == {
        "usage_files": 0,
        "exchange_files": 0,
        "event_files": 0,
    }
