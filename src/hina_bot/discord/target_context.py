import logging
import re
from contextvars import ContextVar
from datetime import timedelta

import discord

log = logging.getLogger("hina")
TARGET_CONTEXT = ContextVar("target_context", default=())
TARGET_QUERY = re.compile(
    r"(?:어떤\s*(?:사람|애|분|유저)|어떤\s*(?:거|것)\s*같|(?:어떻게|뭐라고)\s*생각|성격|인상|평가|평판)",
    re.IGNORECASE,
)


def targets(message, bot_id):
    rows, seen = [], set()
    for user in getattr(message, "mentions", ()):
        uid = getattr(user, "id", None)
        if uid is None or uid in seen or uid in {bot_id, message.author.id} or getattr(user, "bot", False):
            continue
        seen.add(uid)
        rows.append(user)
        if len(rows) == 2:
            break
    return rows


async def collect(message, bot_id, text):
    if getattr(message, "guild", None) is None or not TARGET_QUERY.search(text):
        return []
    selected = targets(message, bot_id)
    if not selected or not hasattr(message.channel, "history"):
        return []
    if hasattr(message.channel, "permissions_for"):
        p = message.channel.permissions_for(message.author)
        if not (getattr(p, "view_channel", True) and getattr(p, "read_message_history", True)):
            return []

    chosen = {u.id: u for u in selected}
    found = {u.id: [] for u in selected}
    sizes = {u.id: 0 for u in selected}
    after = message.created_at - timedelta(days=7)
    try:
        async for old in message.channel.history(limit=300, before=message, after=after, oldest_first=False):
            uid = getattr(getattr(old, "author", None), "id", None)
            if uid not in chosen or getattr(old, "webhook_id", None) is not None:
                continue
            text_value = (getattr(old, "content", "") or "").strip()
            if not text_value or len(found[uid]) >= 8 or sizes[uid] >= 2400:
                continue
            text_value = text_value[:2400 - sizes[uid]]
            sizes[uid] += len(text_value)
            found[uid].append({
                "message_id": str(getattr(old, "id", "")),
                "at": old.created_at.isoformat() if getattr(old, "created_at", None) else "",
                "content": text_value,
            })
            if all(len(found[x]) >= 8 or sizes[x] >= 2400 for x in chosen):
                break
    except discord.HTTPException as exc:
        log.warning("Target context lookup failed (%s)", type(exc).__name__)
        return []

    return [{
        "user_id": str(u.id),
        "name": getattr(u, "display_name", getattr(u, "name", ""))[:100],
        "channel_id": str(getattr(message.channel, "id", "")),
        "sampled_messages": list(reversed(found[u.id])),
    } for u in selected]
