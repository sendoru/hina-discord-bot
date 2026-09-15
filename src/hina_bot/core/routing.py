import re
from dataclasses import dataclass

_CALL_PUNCTUATION = ",，:：!！?？~"


@dataclass(frozen=True)
class Scope:
    guild_id: int | None
    channel_id: int
    user_id: int
    public_at_capture: bool = False

    @property
    def realm(self):
        return f"guild:{self.guild_id}" if self.guild_id is not None else f"dm:{self.user_id}"

    @property
    def channel(self):
        return f"{self.realm}:channel:{self.channel_id}"

    @property
    def conversation(self):
        return f"{self.realm}:channel:{self.channel_id}:user:{self.user_id}"

    @property
    def user_note(self):
        return f"{self.realm}:user:{self.user_id}"


def _matched_prefix(text: str, prefixes: tuple[str, ...]) -> str | None:
    return max((prefix for prefix in prefixes if text.startswith(prefix)), key=len, default=None)


def _strip_boundary_bot_mentions(text: str, bot_id: int) -> str:
    mention = rf"<@!?{bot_id}>"
    punctuation = re.escape(_CALL_PUNCTUATION)
    text = re.sub(rf"^(?:{mention}\s*[{punctuation}]*\s*)+", "", text)
    text = re.sub(
        rf"(?:\s*[{punctuation}]*\s*{mention}\s*[{punctuation}]*)+$",
        "",
        text,
    )
    return text.strip()


def trigger_text(message, bot_id: int, dm_always_reply: bool = False,
                 prefixes: tuple[str, ...] = ("히나야",)) -> str | None:
    if message.author.bot or message.webhook_id is not None:
        return None
    raw = message.content.lstrip()
    # Discord includes the replied-to author in mentions only when reply ping is enabled.
    # Do not infer a ping merely from message.reference.
    ping = any(user.id == bot_id for user in message.mentions)
    matched = _matched_prefix(raw, prefixes)
    keyword = matched is not None
    implicit_dm = message.guild is None and dm_always_reply
    if not (ping or keyword or implicit_dm):
        return None

    # A Discord mention at the start or end is call syntax. Mentions in the middle may be
    # meaningful sentence content, so leave them intact for the model.
    text = _strip_boundary_bot_mentions(raw, bot_id)

    # Natural-language call prefixes are also part of what the user actually said. Preserve
    # them for the model, except when the message contains only the prefix (plus call punctuation),
    # which keeps the existing bare-call fast path.
    matched = _matched_prefix(text, prefixes)
    if matched is not None:
        remainder = text[len(matched):]
        if not remainder.strip(f" \t\n{_CALL_PUNCTUATION}"):
            return ""
    return text


def chunks(text: str, limit: int = 1900):
    # Count UTF-16 units conservatively, including emoji; Discord limit is 2000.
    part, size = [], 0
    for char in re.findall(r"<a?:[^:<>\s]{1,32}:\d{1,20}>|.", text, flags=re.DOTALL):
        n = len(char.encode("utf-16-le")) // 2
        if size + n > limit:
            yield "".join(part)
            part, size = [], 0
        part.append(char)
        size += n
    if part:
        yield "".join(part)
