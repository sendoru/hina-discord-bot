"""Bounded provenance for the user request that produced a delivered assistant turn."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime

from ..ai.vision import VisualInput
from .vision import message_has_visual

CURRENT_TURN_PROVENANCE: ContextVar[dict | None] = ContextVar(
    "current_turn_provenance",
    default=None,
)

_MAX_ORIGIN_SOURCES = 2
_PROVENANCE_VISUAL_CONTEXT_KINDS = {
    "current_message",
    "replied_message",
    "reply_reference_source",
    "reply_origin_source",
    "reply_origin_request",
    "prior_reply_source",
    "speaker_thread",
    "target_user_history",
}


def _author_row(message) -> dict:
    author = getattr(message, "author", None)
    user_id = str(getattr(author, "id", "") or "")
    return {
        "user_id": user_id,
        "author_user_id": user_id,
        "name": str(
            getattr(author, "display_name", getattr(author, "name", "")) or ""
        )[:100],
        "role": "bot" if getattr(author, "bot", False) else "user",
    }


def _source_at(value: dict) -> str:
    at = str(value.get("at") or "").strip()
    if at:
        return at
    unix_time = value.get("unix_time")
    if isinstance(unix_time, (int, float)) and not isinstance(unix_time, bool):
        return datetime.fromtimestamp(float(unix_time), UTC).isoformat()
    return ""


def _source_from_visual(visual: VisualInput) -> dict:
    return {
        "message_id": str(visual.message_id or ""),
        "user_id": str(visual.author_user_id or ""),
        "author_user_id": str(visual.author_user_id or ""),
        "name": str(visual.author_name or "")[:100],
        "content": str(visual.message_content or "")[:2000],
        "role": "user",
        "direct_trigger": None,
        "has_visual": True,
        "at": str(visual.at or ""),
    }


def build_turn_provenance(
    message,
    visible_content: str,
    replied: list[dict] | tuple[dict, ...],
    visuals: list[VisualInput] | tuple[VisualInput, ...],
) -> dict:
    """Build a small, flat record; image bytes and recursive provenance never enter it."""
    message_id = str(getattr(message, "id", "") or "")
    created_at = getattr(message, "created_at", None)
    request = {
        "message_id": message_id,
        **_author_row(message),
        "content": str(visible_content or "")[:4000],
        "direct_trigger": True,
        "provenance_class": "conversation",
        "at": created_at.isoformat() if created_at is not None else "",
        "has_visual": message_has_visual(message),
    }

    sources: list[dict] = []
    seen: set[str] = {message_id}

    def add_source(value: dict) -> None:
        source_id = str(value.get("message_id", ""))
        if not source_id or source_id in seen or len(sources) >= _MAX_ORIGIN_SOURCES:
            return
        seen.add(source_id)
        author_id = str(value.get("author_user_id") or value.get("user_id") or "")
        role = str(value.get("role") or "user")
        sources.append({
            "message_id": source_id,
            "user_id": author_id,
            "author_user_id": author_id,
            "name": str(value.get("name") or value.get("author_name") or "")[:100],
            "content": str(value.get("content") or value.get("message_content") or "")[:2000],
            "role": role,
            "direct_trigger": value.get("direct_trigger"),
            "at": _source_at(value),
            "has_visual": bool(value.get("has_visual", False)),
            "provenance_class": (
                "conversation"
                if role == "assistant" or author_id == request["author_user_id"]
                else "reference_material"
            ),
        })

    for row in replied:
        add_source(dict(row))
    for visual in visuals:
        if visual.context_kind not in _PROVENANCE_VISUAL_CONTEXT_KINDS:
            continue
        source = _source_from_visual(visual)
        source_id = source["message_id"]
        if source_id in seen:
            if source_id == message_id:
                request["has_visual"] = True
            else:
                for existing in sources:
                    if existing["message_id"] == source_id:
                        existing["has_visual"] = True
                        if not existing["content"]:
                            existing["content"] = source["content"]
                        if not existing.get("at"):
                            existing["at"] = source["at"]
                        break
            continue
        add_source(source)

    return {
        "origin_request": request,
        "origin_sources": sources,
    }


__all__ = ["CURRENT_TURN_PROVENANCE", "build_turn_provenance"]
