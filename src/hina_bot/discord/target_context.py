import logging
import re
from contextvars import ContextVar
from datetime import timedelta

import discord

from .routing import trigger_text

log = logging.getLogger("hina")
TARGET_CONTEXT = ContextVar("target_context", default=())

# Mentioning another user is itself enough to make a small amount of that user's recent channel
# history useful. These patterns only decide whether to expand that request-scoped lookup; they no
# longer decide whether targeted context exists at all.
TARGET_PROFILE_QUERY = re.compile(
    r"(?:어떤\s*(?:사람|애|분|유저)|어떤\s*(?:거|것)\s*같|(?:어떻게|뭐라고)\s*생각|성격|인상|평가|평판)",
    re.IGNORECASE,
)
TARGET_HISTORY_QUERY = re.compile(
    r"(?:채팅|대화|메시지|발언|기록|로그).{0,18}(?:읽|봐|보자|훑|분석|요약|찾|확인|살펴)|"
    r"(?:읽|훑|분석|요약|살펴|확인).{0,18}(?:채팅|대화|메시지|발언|기록|로그)|"
    r"(?:최근|예전|평소).{0,12}(?:뭐|무슨|어떤)?\s*(?:말|얘기).{0,12}(?:했|하|해)",
    re.IGNORECASE,
)

_BASIC_HISTORY_LIMIT = 120
_BASIC_HISTORY_DAYS = 1
_BASIC_MESSAGE_LIMIT = 3
_BASIC_CHAR_LIMIT = 1200
_DEEP_HISTORY_LIMIT = 300
_DEEP_HISTORY_DAYS = 7
_DEEP_MESSAGE_LIMIT = 8
_DEEP_CHAR_LIMIT = 2400


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


def _retrieval_limits(text: str) -> tuple[bool, bool, int, int, int, int]:
    explicit_history = bool(TARGET_HISTORY_QUERY.search(text))
    deep = explicit_history or bool(TARGET_PROFILE_QUERY.search(text))
    if deep:
        return (
            True,
            explicit_history,
            _DEEP_HISTORY_LIMIT,
            _DEEP_HISTORY_DAYS,
            _DEEP_MESSAGE_LIMIT,
            _DEEP_CHAR_LIMIT,
        )
    return (
        False,
        False,
        _BASIC_HISTORY_LIMIT,
        _BASIC_HISTORY_DAYS,
        _BASIC_MESSAGE_LIMIT,
        _BASIC_CHAR_LIMIT,
    )


async def collect(
    message,
    bot_id,
    text,
    *,
    direct_only: bool = False,
    call_prefixes: tuple[str, ...] = ("히나야",),
):
    if getattr(message, "guild", None) is None:
        return []
    selected = targets(message, bot_id)
    if not selected or not hasattr(message.channel, "history"):
        return []
    if hasattr(message.channel, "permissions_for"):
        p = message.channel.permissions_for(message.author)
        if not (getattr(p, "view_channel", True) and getattr(p, "read_message_history", True)):
            return []

    deep, explicit_history, history_limit, history_days, message_limit, char_limit = (
        _retrieval_limits(text)
    )
    # `capture=direct` prevents passive side-chat collection. An explicit request to inspect a
    # mentioned user's history is different: read that user's visible messages for this request
    # only, without adding them to the rolling recent buffer.
    require_direct_trigger = direct_only and not explicit_history

    chosen = {u.id: u for u in selected}
    found = {u.id: [] for u in selected}
    sizes = {u.id: 0 for u in selected}
    after = message.created_at - timedelta(days=history_days)
    try:
        async for old in message.channel.history(
            limit=history_limit,
            before=message,
            after=after,
            oldest_first=False,
        ):
            uid = getattr(getattr(old, "author", None), "id", None)
            if uid not in chosen or getattr(old, "webhook_id", None) is not None:
                continue
            if require_direct_trigger and trigger_text(old, bot_id, False, call_prefixes) is None:
                continue
            text_value = (getattr(old, "content", "") or "").strip()
            if not text_value or len(found[uid]) >= message_limit or sizes[uid] >= char_limit:
                continue
            text_value = text_value[:char_limit - sizes[uid]]
            sizes[uid] += len(text_value)
            found[uid].append({
                "message_id": str(getattr(old, "id", "")),
                "at": old.created_at.isoformat() if getattr(old, "created_at", None) else "",
                "content": text_value,
            })
            if all(len(found[x]) >= message_limit or sizes[x] >= char_limit for x in chosen):
                break
    except discord.HTTPException as exc:
        log.warning("Target context lookup failed (%s)", type(exc).__name__)
        return []

    mode = "deep" if deep else "basic"
    return [{
        "user_id": str(u.id),
        "name": getattr(u, "display_name", getattr(u, "name", ""))[:100],
        "channel_id": str(getattr(message.channel, "id", "")),
        "retrieval_mode": mode,
        "explicit_history_request": explicit_history,
        "sampled_messages": list(reversed(found[u.id])),
    } for u in selected if found[u.id]]
