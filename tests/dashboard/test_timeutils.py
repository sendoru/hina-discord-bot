from datetime import UTC, datetime

from hina_bot.dashboard.timeutils import (
    db_utc_timestamp,
    format_local_time,
    parse_local_time,
    quick_ranges,
    telemetry_freshness,
)


def test_local_dashboard_time_converts_to_utc_for_queries():
    parsed = parse_local_time("2026-09-22T22:30", "Asia/Seoul")

    assert parsed == datetime(2026, 9, 22, 13, 30, tzinfo=UTC)
    assert db_utc_timestamp("2026-09-22T22:30", "Asia/Seoul") == "2026-09-22 13:30:00"


def test_localtime_formats_naive_sqlite_timestamp_as_utc():
    assert (
        format_local_time("2026-09-22 13:30:00", "Asia/Seoul")
        == "2026-09-22 22:30:00 KST"
    )


def test_quick_ranges_are_local_datetime_values():
    ranges = quick_ranges(
        "Asia/Seoul",
        now=datetime(2026, 9, 22, 22, 30, tzinfo=UTC).astimezone(),
    )

    labels = [row["label"] for row in ranges]
    assert labels == ["Today", "1h", "24h", "7d", "30d"]
    assert all("T" in row["after"] for row in ranges)


def test_telemetry_freshness_reports_age_bands():
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    assert telemetry_freshness("2026-09-22T11:30:00+00:00", now=now) == {
        "label": "Fresh",
        "tone": "good",
        "age_label": "Last event 30m ago",
    }
    assert telemetry_freshness("2026-09-22T06:00:00+00:00", now=now) == {
        "label": "Delayed",
        "tone": "warn",
        "age_label": "Last event 6h ago",
    }
    assert telemetry_freshness("2026-09-20T12:00:00+00:00", now=now) == {
        "label": "Stale",
        "tone": "danger",
        "age_label": "Last event 2d ago",
    }
    assert telemetry_freshness(None, now=now) == {
        "label": "Unknown",
        "tone": "neutral",
        "age_label": "No retained events",
    }
