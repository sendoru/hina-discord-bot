"""Deterministic Discord output guards; model instructions are not a security boundary."""
import os
import re

# Broadcast and role mentions are always inert. Ordinary user mentions can be enabled separately.
# Discord's AllowedMentions configuration provides a second boundary at send time.
DISCORD_MENTION = re.compile(r"@(?:everyone|here)|<@&\d{1,20}>", re.IGNORECASE)
USER_MENTION = re.compile(r"<@!?\d{1,20}>")


def user_mentions_enabled() -> bool:
    """Return whether generated ordinary-user mentions should remain live.

    This is intentionally a startup/environment-level switch. Unknown values fail closed instead of
    accidentally enabling notifications.
    """
    return os.getenv("ALLOW_USER_MENTIONS", "true").strip().lower() == "true"


def neutralize_mentions(text: str, *, allow_user_mentions: bool | None = None) -> str:
    result = DISCORD_MENTION.sub(lambda match: match.group(0).replace("@", "＠"), text)
    if allow_user_mentions is None:
        allow_user_mentions = user_mentions_enabled()
    if not allow_user_mentions:
        result = USER_MENTION.sub(lambda match: match.group(0).replace("@", "＠"), result)
    return result
