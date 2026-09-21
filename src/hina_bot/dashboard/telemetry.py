from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TelemetrySnapshot:
    usage: tuple[dict[str, object], ...]
    exchanges: tuple[dict[str, object], ...]
    events: tuple[dict[str, object], ...]
    oldest_at: str | None
    newest_at: str | None


@dataclass(frozen=True)
class TraceTelemetry:
    turn_id: str
    usage: tuple[dict[str, object], ...]
    exchanges: tuple[dict[str, object], ...]
    events: tuple[dict[str, object], ...]


class TelemetryReader:
    """Read the current and rotated content-free JSONL telemetry files."""

    def __init__(
        self,
        usage_log_path: str | Path | None,
        event_log_path: str | Path | None,
        *,
        backup_count: int = 3,
    ):
        if backup_count < 0:
            raise ValueError("backup_count must not be negative")
        self.usage_path = self._path(usage_log_path)
        self.event_path = self._path(event_log_path)
        self.exchange_path = (
            self.usage_path.with_name("discord-usage.jsonl") if self.usage_path else None
        )
        self.backup_count = int(backup_count)

    @staticmethod
    def _path(value: str | Path | None) -> Path | None:
        if value is None:
            return None
        text = str(value).strip()
        return Path(text).expanduser() if text else None

    def _rotated_paths(self, base: Path | None) -> tuple[Path, ...]:
        if base is None:
            return ()
        candidates = [Path(f"{base}.{index}") for index in range(self.backup_count, 0, -1)]
        candidates.append(base)
        return tuple(path for path in candidates if path.is_file())

    def _read(self, base: Path | None) -> tuple[dict[str, object], ...]:
        rows: list[dict[str, object]] = []
        for path in self._rotated_paths(base):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            value = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        if isinstance(value, dict):
                            rows.append(value)
            except OSError:
                # Rotation can rename a file between discovery and open. A later refresh can read it.
                continue
        return tuple(rows)

    @staticmethod
    def _range(*streams: tuple[dict[str, object], ...]) -> tuple[str | None, str | None]:
        timestamps = [
            value
            for stream in streams
            for row in stream
            if isinstance((value := row.get("at")), str) and value
        ]
        if not timestamps:
            return None, None
        return min(timestamps), max(timestamps)

    def snapshot(self) -> TelemetrySnapshot:
        usage = self._read(self.usage_path)
        exchanges = self._read(self.exchange_path)
        events = self._read(self.event_path)
        oldest_at, newest_at = self._range(usage, exchanges, events)
        return TelemetrySnapshot(
            usage=usage,
            exchanges=exchanges,
            events=events,
            oldest_at=oldest_at,
            newest_at=newest_at,
        )

    def for_turn(self, turn_id: str) -> TraceTelemetry:
        trace_id = turn_id.strip()
        if not trace_id:
            raise ValueError("turn_id must not be empty")
        snapshot = self.snapshot()

        def matching(rows):
            return tuple(row for row in rows if row.get("turn_id") == trace_id)

        return TraceTelemetry(
            turn_id=trace_id,
            usage=matching(snapshot.usage),
            exchanges=matching(snapshot.exchanges),
            events=matching(snapshot.events),
        )

    def source_status(self) -> dict[str, int]:
        return {
            "usage_files": len(self._rotated_paths(self.usage_path)),
            "exchange_files": len(self._rotated_paths(self.exchange_path)),
            "event_files": len(self._rotated_paths(self.event_path)),
        }
