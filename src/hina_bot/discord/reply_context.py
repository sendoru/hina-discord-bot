import logging
from contextvars import ContextVar

import discord

log = logging.getLogger("hina")

REPLY_CONTEXT = ContextVar("reply_context", default=())


def _row(target, bot_id: int) -> dict | None:
    content = (getattr(target, "content", "") or "").strip()
    author = getattr(target, "author", None)
    user_id = getattr(author, "id", None)
    if not content or author is None or user_id is None:
        return None

    own_bot = user_id == bot_id
    other_bot = bool(getattr(author, "bot", False)) or getattr(target, "webhook_id", None) is not None
    role = "assistant" if own_bot else ("bot" if other_bot else "user")
    created_at = getattr(target, "created_at", None)
    return {
        "message_id": str(getattr(target, "id", "")),
        "user_id": str(user_id),
        "name": str(getattr(author, "display_name", getattr(author, "name", "")))[:100],
        "content": content[:4000],
        "role": role,
        "context_kind": "replied_message",
        "reference_strength": "explicit_reply",
        "at": created_at.isoformat() if created_at is not None else "",
    }


async def collect_reply_context(message, bot_id: int) -> list[dict]:
    """Return the message explicitly replied to by the current invocation, if readable.

    This is request-scoped context rather than passive chat-log capture. In particular, a reply
    remains available even when `/chatlog capture direct` would normally omit the replied-to
    message from the rolling channel buffer.
    """
    reference = getattr(message, "reference", None)
    if reference is None:
        return []

    channel = getattr(message, "channel", None)
    current_channel_id = getattr(channel, "id", None)
    reference_channel_id = getattr(reference, "channel_id", None)
    if (
        current_channel_id is not None
        and reference_channel_id is not None
        and current_channel_id != reference_channel_id
    ):
        return []

    target = getattr(reference, "resolved", None)
    if target is not None and getattr(target, "author", None) is None:
        target = None

    if target is None:
        message_id = getattr(reference, "message_id", None)
        fetch_message = getattr(channel, "fetch_message", None)
        if message_id is None or fetch_message is None:
            return []
        try:
            target = await fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            log.warning("Reply context lookup failed (%s)", type(exc).__name__)
            return []

    target_channel = getattr(target, "channel", None)
    target_channel_id = getattr(target_channel, "id", current_channel_id)
    if (
        current_channel_id is not None
        and target_channel_id is not None
        and current_channel_id != target_channel_id
    ):
        return []

    row = _row(target, bot_id)
    return [row] if row is not None else []
