"""Helpers for exporting verbose admin data as Discord attachments."""

import io
from datetime import UTC, datetime

import discord


def text_attachment(content: str, filename: str) -> discord.File:
    """Build an in-memory UTF-8 text attachment for an ephemeral admin response."""
    payload = io.BytesIO(content.encode("utf-8"))
    return discord.File(payload, filename=filename)


def export_timestamp(value: str | None) -> str:
    """Render stored ISO timestamps as human-readable UTC text for downloaded files."""
    if not isinstance(value, str) or not value:
        return "시간 미기록"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")


__all__ = ["export_timestamp", "text_attachment"]
