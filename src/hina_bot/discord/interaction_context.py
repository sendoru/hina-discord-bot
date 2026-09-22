"""Build bounded structural metadata for the current Discord message."""

from __future__ import annotations

_MAX_MENTIONS = 12


def _identity(user, bot_id: int, *, include_name: bool = True) -> dict:
    user_id = str(getattr(user, "id", "") or "")
    is_self = user_id == str(bot_id)
    return {
        "user_id": user_id,
        "name": (
            str(getattr(user, "display_name", getattr(user, "name", "")) or "")[:100]
            if include_name or is_self
            else ""
        ),
        "is_bot": bool(getattr(user, "bot", False)),
        "is_self": is_self,
    }


def build_interaction_context(
    message,
    bot_id: int,
    replied=(),
    *,
    include_mention_names: bool = True,
) -> dict:
    """Return only Discord-known structure; semantic roles stay model-owned."""
    author = getattr(message, "author", None)
    mentions = []
    seen = set()
    for user in getattr(message, "mentions", ()) or ():
        user_id = str(getattr(user, "id", "") or "")
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        mentions.append(
            _identity(user, bot_id, include_name=include_mention_names)
        )
        if len(mentions) >= _MAX_MENTIONS:
            break

    reply_target = None
    for row in replied or ():
        user_id = str(row.get("author_user_id") or row.get("user_id") or "")
        if not user_id:
            continue
        reply_target = {
            "user_id": user_id,
            "name": str(row.get("name") or "")[:100],
            "role": str(row.get("role") or "user"),
            "is_self": user_id == str(bot_id),
        }
        break

    return {
        "speaker": _identity(author, bot_id) if author is not None else None,
        "mentions": mentions,
        "reply_target": reply_target,
    }


__all__ = ["build_interaction_context"]
