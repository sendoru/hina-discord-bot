from datetime import UTC, datetime, timedelta

from hina_bot.core.conversation_session import active_session_turns

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def row(message_id, at):
    return {"message_id": str(message_id), "created_at": at}


def test_active_session_uses_consecutive_gaps_not_total_elapsed_time():
    turns = [
        row(1, NOW - timedelta(hours=9)),
        row(2, NOW - timedelta(hours=6)),
        row(3, NOW - timedelta(hours=3)),
    ]
    # Each consecutive gap is <= 4h, even though oldest turn is 9h old.
    assert [r["message_id"] for r in active_session_turns(
        turns, gap_minutes=240, now=NOW
    )] == ["1", "2", "3"]


def test_gap_to_last_turn_starts_a_new_session():
    assert active_session_turns(
        [row(1, NOW - timedelta(hours=4, seconds=1))],
        gap_minutes=240,
        now=NOW,
    ) == []


def test_gap_between_turns_stops_without_backfilling_old_topics():
    turns = [
        row(1, NOW - timedelta(hours=7)),
        row(2, NOW - timedelta(hours=2)),
        row(3, NOW - timedelta(hours=1)),
    ]
    assert [r["message_id"] for r in active_session_turns(
        turns, gap_minutes=240, now=NOW
    )] == ["2", "3"]


def test_exact_gap_and_24_hour_boundaries_are_inclusive():
    turns = [
        row(1, NOW - timedelta(hours=24)),
        row(2, NOW - timedelta(hours=20)),
        row(3, NOW - timedelta(hours=16)),
        row(4, NOW - timedelta(hours=12)),
        row(5, NOW - timedelta(hours=8)),
        row(6, NOW - timedelta(hours=4)),
    ]
    assert len(active_session_turns(turns, gap_minutes=240, now=NOW)) == 6


def test_24_hour_absolute_cap_stops_rolling_context():
    turns = [
        row(1, NOW - timedelta(hours=28)),
        row(2, NOW - timedelta(hours=24)),
        row(3, NOW - timedelta(hours=20)),
        row(4, NOW - timedelta(hours=16)),
        row(5, NOW - timedelta(hours=12)),
        row(6, NOW - timedelta(hours=8)),
        row(7, NOW - timedelta(hours=4)),
    ]
    assert [r["message_id"] for r in active_session_turns(
        turns, gap_minutes=240, now=NOW
    )] == ["2", "3", "4", "5", "6", "7"]


def test_invalid_and_future_timestamps_fail_closed():
    for invalid in ("not-a-time", "", None, NOW + timedelta(minutes=1)):
        assert active_session_turns(
            [row(1, NOW - timedelta(minutes=15)), row(2, invalid)],
            now=NOW,
        ) == []


def test_sqlite_utc_naive_and_iso_offsets_work():
    turns = [
        row(1, "2026-11-11 10:00:00"),
        row(2, "2026-11-11T20:30:00+09:00"),
    ]
    assert [r["message_id"] for r in active_session_turns(
        turns, now=NOW
    )] == ["1", "2"]
