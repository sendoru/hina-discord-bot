from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def parse_local_time(value: str, timezone: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed.astimezone(UTC)


def utc_iso(value: str, timezone: str) -> str:
    parsed = parse_local_time(value, timezone)
    return parsed.isoformat(timespec="seconds") if parsed is not None else ""


def db_utc_timestamp(value: str, timezone: str) -> str:
    parsed = parse_local_time(value, timezone)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed is not None else ""


def _parse_stored_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def format_local_time(value: object, timezone: str) -> str:
    parsed = _parse_stored_time(value)
    if parsed is None:
        return "—"
    local = parsed.astimezone(ZoneInfo(timezone))
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")


def local_input_value(value: str, timezone: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    parsed = parse_local_time(text, timezone)
    if parsed is None:
        return ""
    return parsed.astimezone(ZoneInfo(timezone)).strftime("%Y-%m-%dT%H:%M")


def quick_ranges(
    timezone: str,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, str], ...]:
    zone = ZoneInfo(timezone)
    current = now.astimezone(zone) if now is not None else datetime.now(zone)
    values = (
        ("Today", current.replace(hour=0, minute=0, second=0, microsecond=0)),
        ("1h", current - timedelta(hours=1)),
        ("24h", current - timedelta(hours=24)),
        ("7d", current - timedelta(days=7)),
        ("30d", current - timedelta(days=30)),
    )
    return tuple(
        {
            "label": label,
            "after": start.strftime("%Y-%m-%dT%H:%M"),
        }
        for label, start in values
    )


__all__ = [
    "db_utc_timestamp",
    "format_local_time",
    "local_input_value",
    "parse_local_time",
    "quick_ranges",
    "utc_iso",
]
