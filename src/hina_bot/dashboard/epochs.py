from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .telemetry import TelemetrySnapshot


@dataclass(frozen=True)
class EpochWindow:
    value: str
    label: str
    start_at: str | None
    end_at: str | None
    current: bool = False


@dataclass(frozen=True)
class EpochSelection:
    snapshot: TelemetrySnapshot
    selected: str
    current: str
    tracked: bool
    windows: tuple[EpochWindow, ...]
    reset_at: str | None
    excluded_rows: int


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalized_epochs(rows: list[dict[str, object]]) -> list[tuple[int, str, datetime]]:
    result = []
    for row in rows:
        try:
            epoch_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        reset_at = str(row.get("reset_at") or "")
        reset_dt = _parse_time(reset_at)
        if epoch_id <= 0 or reset_dt is None:
            continue
        result.append((epoch_id, reset_at, reset_dt))
    result.sort(key=lambda item: (item[2], item[0]))
    return result


def _row_in_window(
    row: dict[str, object],
    *,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    at = _parse_time(row.get("at"))
    if at is None:
        return False
    if start is not None and at < start:
        return False
    return not (end is not None and at >= end)


def _filtered_range(
    usage: tuple[dict[str, object], ...],
    exchanges: tuple[dict[str, object], ...],
    events: tuple[dict[str, object], ...],
) -> tuple[str | None, str | None]:
    timestamps = [
        str(row["at"])
        for stream in (usage, exchanges, events)
        for row in stream
        if _parse_time(row.get("at")) is not None
    ]
    if not timestamps:
        return None, None
    parsed = sorted((_parse_time(value), value) for value in timestamps)
    return parsed[0][1], parsed[-1][1]


def select_observability_epoch(
    snapshot: TelemetrySnapshot,
    epoch_rows: list[dict[str, object]],
    requested: str = "",
) -> EpochSelection:
    epochs = _normalized_epochs(epoch_rows)
    if not epochs:
        return EpochSelection(
            snapshot=snapshot,
            selected="all",
            current="",
            tracked=False,
            windows=(
                EpochWindow("all", "All retained telemetry", None, None, True),
            ),
            reset_at=None,
            excluded_rows=0,
        )

    current_id, current_reset_at, _ = epochs[-1]
    current = str(current_id)
    windows: list[EpochWindow] = [
        EpochWindow("all", "All retained telemetry", None, None, False),
        EpochWindow(
            "legacy",
            "Legacy / before first reset",
            None,
            epochs[0][1],
            False,
        ),
    ]
    for index, (epoch_id, reset_at, _) in enumerate(epochs):
        end_at = epochs[index + 1][1] if index + 1 < len(epochs) else None
        windows.append(
            EpochWindow(
                str(epoch_id),
                f"Epoch #{epoch_id}",
                reset_at,
                end_at,
                epoch_id == current_id,
            )
        )

    requested = requested.strip().lower()
    valid = {window.value for window in windows}
    selected = requested if requested in valid else current

    if selected == "all":
        start = end = None
        filtered = snapshot
    elif selected == "legacy":
        start = None
        end = epochs[0][2]
        filtered = None
    else:
        selected_id = int(selected)
        index = next(i for i, item in enumerate(epochs) if item[0] == selected_id)
        start = epochs[index][2]
        end = epochs[index + 1][2] if index + 1 < len(epochs) else None
        filtered = None

    if filtered is None:
        usage = tuple(
            row for row in snapshot.usage
            if _row_in_window(row, start=start, end=end)
        )
        exchanges = tuple(
            row for row in snapshot.exchanges
            if _row_in_window(row, start=start, end=end)
        )
        events = tuple(
            row for row in snapshot.events
            if _row_in_window(row, start=start, end=end)
        )
        oldest_at, newest_at = _filtered_range(usage, exchanges, events)
        filtered = TelemetrySnapshot(
            usage=usage,
            exchanges=exchanges,
            events=events,
            oldest_at=oldest_at,
            newest_at=newest_at,
        )

    total_before = len(snapshot.usage) + len(snapshot.exchanges) + len(snapshot.events)
    total_after = (
        len(filtered.usage)
        + len(filtered.exchanges)
        + len(filtered.events)
    )
    reset_at = next(
        (
            window.start_at
            for window in windows
            if window.value == selected and window.start_at
        ),
        None,
    )
    return EpochSelection(
        snapshot=filtered,
        selected=selected,
        current=current,
        tracked=True,
        windows=tuple(windows),
        reset_at=reset_at,
        excluded_rows=max(0, total_before - total_after),
    )


def epoch_view(selection: EpochSelection) -> dict[str, object]:
    return {
        "tracked": selection.tracked,
        "selected": selection.selected,
        "current": selection.current,
        "reset_at": selection.reset_at,
        "excluded_rows": selection.excluded_rows,
        "is_current": bool(
            selection.tracked and selection.selected == selection.current
        ),
        "windows": tuple(
            {
                "value": window.value,
                "label": window.label,
                "start_at": window.start_at,
                "end_at": window.end_at,
                "current": window.current,
            }
            for window in selection.windows
        ),
    }


__all__ = [
    "EpochSelection",
    "EpochWindow",
    "epoch_view",
    "select_observability_epoch",
]
