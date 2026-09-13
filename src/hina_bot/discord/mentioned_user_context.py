import logging
from datetime import timedelta

import discord

log = logging.getLogger("hina")

_LOOKBACK = timedelta(days=7)
_SCAN_LIMIT = 300
_MAX_TARGETS = 2
_MAX_MESSAGES_PER_TARGET = 8
_MAX_CHARS_PER_TARGET = 2400


def mentioned_targets(message, bot_id: int):
    """Return a small, stable set of explicitly mentioned non-bot users."""
    result = []
    seen = set()
    for user in getattr(message, "mentions", ()):
        user_id = getattr(user, "id", None)
        if user_id is None or user_id in seen or user_id in {bot_id, message.author.id}:
            continue
        if getattr(user, "bot", False):
            continue
        seen.add(user_id)
        result.append(user)
        if len(result) >= _MAX_TARGETS:
            break
    return result


async def collect_mentioned_user_history(message, bot_id: int) -> list[dict]:
    """Sample recent messages by explicitly mentioned users from this channel only.

    This is deliberately bounded and ephemeral. It does not write anything to long-term memory,
    does not cross channels, and only runs when the caller can read this channel's history.
    """
    if getattr(message, "guild", None) is None:
        return []
    targets = mentioned_targets(message, bot_id)
    if not targets:
        return []

    channel = message.channel
    history = getattr(channel, "history", None)
    created_at = getattr(message, "created_at", None)
    if history is None or created_at is None:
        return []

    permissions_for = getattr(channel, "permissions_for", None)
    if permissions_for is not None:
        try:
            permissions = permissions_for(message.author)
        except (AttributeError, TypeError):
            return []
        if not getattr(permissions, "view_channel", True):
            return []
        if not getattr(permissions, "read_message_history", True):
            return []

    target_map = {user.id: user for user in targets}
    samples = {user.id: [] for user in targets}
    used_chars = {user.id: 0 for user in targets}
    after = created_at - _LOOKBACK

    try:
        async for old in history(
            limit=_SCAN_LIMIT,
            before=message,
            after=after,
            oldest_first=False,
        ):
            author = getattr(old, "author", None)
            user_id = getattr(author, "id", None)
            if user_id not in target_map:
                continue
            if getattr(old, "webhook_id", None) is not None:
                continue
            content = (getattr(old, "content", "") or "").strip()
            if not content:
                continue
            if len(samples[user_id]) >= _MAX_MESSAGES_PER_TARGET:
                continue

            remaining = _MAX_CHARS_PER_TARGET - used_chars[user_id]
            if remaining <= 0:
                continue
            content = content[:remaining]
            used_chars[user_id] += len(content)
            old_created_at = getattr(old, "created_at", None)
            samples[user_id].append({
                "message_id": str(getattr(old, "id", "")),
                "at": old_created_at.isoformat() if old_created_at is not None else "",
                "content": content,
            })

            if all(
                len(samples[target_id]) >= _MAX_MESSAGES_PER_TARGET
                or used_chars[target_id] >= _MAX_CHARS_PER_TARGET
                for target_id in target_map
            ):
                break
    except discord.HTTPException as exc:
        log.warning("Mentioned-user history lookup failed (%s)", type(exc).__name__)
        return []

    result = []
    for user in targets:
        rows = list(reversed(samples[user.id]))
        result.append({
            "context_kind": "mentioned_user_history",
            "user_id": str(user.id),
            "name": getattr(user, "display_name", getattr(user, "name", ""))[:100],
            "channel_id": str(getattr(channel, "id", "")),
            "lookback_days": _LOOKBACK.days,
            "sampled_messages": rows,
        })
    return result
