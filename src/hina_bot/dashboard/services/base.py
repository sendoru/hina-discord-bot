from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from ..repository import AdminRepository
from ..timeutils import quick_ranges


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _timestamp(row: dict[str, object]) -> str:
    value = row.get("at")
    return value if isinstance(value, str) else ""


def _turn_id(row: dict[str, object]) -> str | None:
    value = row.get("turn_id")
    return value if isinstance(value, str) and value else None


def _decode_json(value: object, default):
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _db_timestamp(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = text.replace("T", " ")
    if len(text) == 16:
        text += ":00"
    return text


def _parse_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class Page:
    number: int
    size: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.size - 1) // self.size)

    @property
    def has_previous(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages


class ReadService:
    def __init__(self, repository: AdminRepository, *, timezone: str = "Asia/Seoul"):
        self.repository = repository
        self.timezone = timezone

    def time_ranges(self) -> tuple[dict[str, str], ...]:
        return quick_ranges(self.timezone)

    @staticmethod
    def _decorate_scope_names(
        row: dict[str, object],
        metadata: dict[str, dict[str, object]],
        *,
        realm_key: str = "realm",
        channel_key: str = "channel_id",
        guild_id_key: str = "guild_id",
    ) -> dict[str, object]:
        """Attach guild/channel names without changing canonical ID fields."""
        realm = str(row.get(realm_key) or "")
        guild_id = str(row.get(guild_id_key) or "")
        if not guild_id and realm.startswith("guild:"):
            guild_id = realm.removeprefix("guild:").split(":", 1)[0]
        channel_id = str(row.get(channel_key) or "")
        scope = str(row.get("scope") or "")
        if scope:
            parts = scope.split(":")
            fields = {
                parts[index]: parts[index + 1]
                for index in range(0, len(parts) - 1, 2)
            }
            if not guild_id:
                guild_id = str(fields.get("guild") or "")
            if not channel_id:
                channel_id = str(fields.get("channel") or "")

        if guild_id and not row.get(guild_id_key):
            row[guild_id_key] = guild_id
        if channel_id and not row.get(channel_key):
            row[channel_key] = channel_id

        guilds = metadata.get("guilds", {})
        channels = metadata.get("channels", {})
        guild_meta = guilds.get(guild_id) if guild_id else None
        channel_meta = channels.get(channel_id) if guild_id and channel_id else None

        row["guild_name"] = (
            str(guild_meta.get("name") or "")
            if isinstance(guild_meta, dict)
            else ""
        )
        row["channel_name"] = (
            str(channel_meta.get("name") or "")
            if isinstance(channel_meta, dict)
            and str(channel_meta.get("guild_id") or "") == guild_id
            else ""
        )
        return row

    @staticmethod
    def _page(number: int, size: int, total: int) -> Page:
        number = max(1, int(number))
        size = min(100, max(10, int(size)))
        return Page(number=number, size=size, total=max(0, int(total)))
