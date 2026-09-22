from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from ..repository import AdminRepository


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
    def __init__(self, repository: AdminRepository):
        self.repository = repository

    @staticmethod
    def _page(number: int, size: int, total: int) -> Page:
        number = max(1, int(number))
        size = min(100, max(10, int(size)))
        return Page(number=number, size=size, total=max(0, int(total)))
