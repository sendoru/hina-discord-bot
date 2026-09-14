import logging
import re
from contextvars import ContextVar
from datetime import timedelta

import discord

from .routing import trigger_text

log = logging.getLogger("hina")
TARGET_CONTEXT = ContextVar("target_context", default=())
TARGET_PROFILE_QUERY = re.compile(
    r"(?:어떤\s*(?:사람|애|분|유저)|어떤\s*(?:거|것)\s*같|(?:어떻게|뭐라고)\s*생각|성격|인상|평가|평판)",
    re.IGNORECASE,
)
TARGET_HISTORY_QUERY = re.compile(
    r"(?:채팅|대화|메시지|발언|기록|로그).{0,18}(?:읽|봐|보자|훑|분석|요약|찾|확인|살펴)|"
    r"(?:읽|훑|분석|요약|살펴|확인).{0,18}(?:채팅|대화|메시지|발언|기록|로그)",
    re.IGNORECASE,
)
TARGET_RECENT_QUERY = re.compile(
    r"(?:아까|방금|최근).{0,18}(?:뭐|무슨|어떤)?\s*(?:말|얘기|메시지|발언).{0,12}(?:했|하|해|였)|"
    r"(?:뭐라고|무슨\s*말(?:을)?).{0,12}(?:했|하|해)(?:어|지|니|나|는지)?",
    re.IGNORECASE,
)

_RETRIEVAL_LIMITS = {
    "basic": {"history": 120, "days": 1, "messages": 3, "chars": 1200},
    "deep": {"history": 300, "days": 7, "messages": 8, "chars": 2400},
}


def retrieval_mode(text: str) -> str | None:
    """Choose request-scoped lookup depth; a bare mention is never sufficient."""
    if TARGET_PROFILE_QUERY.search(text) or TARGET_HISTORY_QUERY.search(text):
        return "deep"
    if TARGET_RECENT_QUERY.search(text):
        return "basic"
    return None


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


async def collect(
    message,
    bot_id,
    text,
    *,
    visibility_mode: str = "all",
    call_prefixes: tuple[str, ...] = ("히나야",),
):
    if visibility_mode not in {"all", "direct", "off"}:
        raise ValueError("visibility_mode must be all, direct, or off")
    mode = retrieval_mode(text)
    if visibility_mode == "off" or getattr(message, "guild", None) is None or mode is None:
        return []
    selected = targets(message, bot_id)
    if not selected or not hasattr(message.channel, "history"):
        return []
    if hasattr(message.channel, "permissions_for"):
        p = message.channel.permissions_for(message.author)
        if not (getattr(p, "view_channel", True) and getattr(p, "read_message_history", True)):
            return []

    limits = _RETRIEVAL_LIMITS[mode]
    chosen = {u.id: u for u in selected}
    found = {u.id: [] for u in selected}
    sizes = {u.id: 0 for u in selected}
    after = message.created_at - timedelta(days=limits["days"])
    try:
        async for old in message.channel.history(
            limit=limits["history"],
            before=message,
            after=after,
            oldest_first=False,
        ):
            uid = getattr(getattr(old, "author", None), "id", None)
            if uid not in chosen or getattr(old, "webhook_id", None) is not None:
                continue
            direct_trigger = trigger_text(old, bot_id, False, call_prefixes) is not None
            if visibility_mode == "direct" and not direct_trigger:
                continue
            text_value = (getattr(old, "content", "") or "").strip()
            if (not text_value or len(found[uid]) >= limits["messages"]
                    or sizes[uid] >= limits["chars"]):
                continue
            text_value = text_value[:limits["chars"] - sizes[uid]]
            sizes[uid] += len(text_value)
            found[uid].append({
                "message_id": str(getattr(old, "id", "")),
                "at": old.created_at.isoformat() if getattr(old, "created_at", None) else "",
                "content": text_value,
                "direct_trigger": direct_trigger,
            })
            if all(len(found[x]) >= limits["messages"]
                   or sizes[x] >= limits["chars"] for x in chosen):
                break
    except discord.HTTPException as exc:
        log.warning("Target context lookup failed (%s)", type(exc).__name__)
        return []

    return [{
        "user_id": str(u.id),
        "name": getattr(u, "display_name", getattr(u, "name", ""))[:100],
        "channel_id": str(getattr(message.channel, "id", "")),
        "retrieval_mode": mode,
        "explicit_history_request": bool(TARGET_HISTORY_QUERY.search(text)),
        "sampled_messages": list(reversed(found[u.id])),
    } for u in selected if found[u.id]]
