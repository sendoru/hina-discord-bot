from hina_bot.dashboard.epochs import (
    epoch_view,
    select_observability_epoch,
)
from hina_bot.dashboard.telemetry import TelemetrySnapshot


def snapshot():
    usage = (
        {"at": "2026-09-01T00:00:00+00:00", "operation": "answer", "model": "legacy"},
        {"at": "2026-09-10T00:00:00+00:00", "operation": "answer", "model": "epoch-1"},
        {"at": "2026-09-20T00:00:00+00:00", "operation": "answer", "model": "epoch-2"},
        {"operation": "answer", "model": "undated"},
    )
    exchanges = (
        {"at": "2026-09-20T00:00:01+00:00", "turn_id": "current"},
    )
    events = (
        {"at": "2026-09-10T00:00:01+00:00", "event": "turn.completed"},
    )
    return TelemetrySnapshot(
        usage=usage,
        exchanges=exchanges,
        events=events,
        oldest_at="2026-09-01T00:00:00+00:00",
        newest_at="2026-09-20T00:00:01+00:00",
    )


def epochs():
    return [
        {"id": 1, "reset_at": "2026-09-05T00:00:00+00:00"},
        {"id": 2, "reset_at": "2026-09-15T00:00:00+00:00"},
    ]


def test_epoch_selection_defaults_to_latest_and_excludes_other_generations():
    selection = select_observability_epoch(snapshot(), epochs())

    assert selection.selected == "2"
    assert selection.current == "2"
    assert [row["model"] for row in selection.snapshot.usage] == ["epoch-2"]
    assert len(selection.snapshot.exchanges) == 1
    assert selection.snapshot.events == ()
    assert selection.excluded_rows == 4

    view = epoch_view(selection)
    assert view["is_current"] is True
    assert view["reset_at"] == "2026-09-15T00:00:00+00:00"
    assert [window["value"] for window in view["windows"]] == [
        "all",
        "legacy",
        "1",
        "2",
    ]


def test_epoch_selection_supports_legacy_historical_and_all():
    legacy = select_observability_epoch(snapshot(), epochs(), "legacy")
    assert [row["model"] for row in legacy.snapshot.usage] == ["legacy"]

    first = select_observability_epoch(snapshot(), epochs(), "1")
    assert [row["model"] for row in first.snapshot.usage] == ["epoch-1"]
    assert len(first.snapshot.events) == 1

    all_rows = select_observability_epoch(snapshot(), epochs(), "all")
    assert len(all_rows.snapshot.usage) == 4
    assert all_rows.excluded_rows == 0


def test_epoch_selection_without_markers_keeps_existing_behavior():
    source = snapshot()
    selection = select_observability_epoch(source, [], "2")

    assert selection.snapshot is source
    assert selection.tracked is False
    assert selection.selected == "all"
    assert selection.current == ""
    assert selection.excluded_rows == 0
