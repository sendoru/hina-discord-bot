"""Deterministic Discord output guards; model instructions are not a security boundary."""
import re

# Keep ordinary user mentions intact, but make broadcast and role mentions inert before delivery.
# Discord's AllowedMentions configuration provides a second boundary at send time.
DISCORD_MENTION = re.compile(r"@(?:everyone|here)|<@&\d{1,20}>", re.IGNORECASE)


def neutralize_mentions(text: str) -> str:
    return DISCORD_MENTION.sub(lambda match: match.group(0).replace("@", "＠"), text)
