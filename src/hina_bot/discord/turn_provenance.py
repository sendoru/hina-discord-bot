"""Bounded provenance for the user request that produced a delivered assistant turn."""

from __future__ import annotations

from contextvars import ContextVar

from ..ai.vision import VisualInput

CURRENT_TURN_PROVENANCE: ContextVar[dict | None] = ContextVar(
    "current_turn_provenance",
    default=None,
)

_MAX_ORIGIN_SOURCES = 2
_STRONG_VISUAL_REFERENCES = {
    "current_message",
    "explicit_reply",
    "prior_explicit_reply",
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
    }


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
    }


def build_turn_provenance(
    message,
    visible_content: str,
    replied: list[dict] | tuple[dict, ...],
    visuals: list[VisualInput] | tuple[VisualInput, ...],
) -> dict:
    """Build a small, flat record; image bytes and recursive provenance never enter it."""
    message_id = str(getattr(message, "id", "") or "")
    request = {
        "message_id": message_id,
        **_author_row(message),
        "content": str(visible_content or "")[:4000],
        "role": "user",
        "direct_trigger": True,
        "has_visual": any(
            visual.message_id == message_id
            and visual.reference_strength == "current_message"
            for visual in visuals
        ),
    }

    sources: list[dict] = []
    seen: set[str] = {message_id}

    def add_source(value: dict) -> None:
        source_id = str(value.get("message_id", ""))
        if not source_id or source_id in seen or len(sources) >= _MAX_ORIGIN_SOURCES:
            return
        seen.add(source_id)
        sources.append({
            "message_id": source_id,
            "user_id": str(value.get("author_user_id") or value.get("user_id") or ""),
            "author_user_id": str(
                value.get("author_user_id") or value.get("user_id") or ""
            ),
            "name": str(value.get("name") or value.get("author_name") or "")[:100],
            "content": str(value.get("content") or value.get("message_content") or "")[:2000],
            "role": str(value.get("role") or "user"),
            "direct_trigger": value.get("direct_trigger"),
            "has_visual": bool(value.get("has_visual", False)),
        })

    for row in replied:
        add_source(dict(row))
    for visual in visuals:
        if visual.reference_strength not in _STRONG_VISUAL_REFERENCES:
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
                        break
            continue
        add_source(source)

    return {
        "origin_request": request,
        "origin_sources": sources,
    }


__all__ = ["CURRENT_TURN_PROVENANCE", "build_turn_provenance"]
