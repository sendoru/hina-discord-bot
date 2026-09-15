"""Only advertise usable custom emoji from the destination guild."""
import re

CUSTOM = re.compile(r"<a?:[^:<>\s]+:(\d+)>")
ALIAS = re.compile(r"(?<![\w:]):([A-Za-z0-9_]{2,32}):(?![\w:])")


def available_emojis(guild, limit=24):
    if guild is None or guild.unavailable or guild.me is None:
        return []
    usable = [e for e in guild.emojis if e.is_usable()]
    usable.sort(key=lambda e: (e.name, e.id))
    return [{"name": e.name, "markup": str(e), "id": str(e.id)} for e in usable[:limit]]


def render_emojis(text, catalog, max_count=2):
    by_id = {e["id"]: e["markup"] for e in catalog}
    by_name = {}
    for e in catalog:
        # Ambiguous names must use their ID, not an arbitrary alias.
        by_name[e["name"]] = e["markup"] if e["name"] not in by_name else None
    text = ALIAS.sub(lambda m: by_name.get(m[1]) or m[0], text)
    used = 0

    def replace(match):
        nonlocal used
        markup = by_id.get(match[1])
        if markup and used < max_count:
            used += 1
            return markup
        return ""

    return CUSTOM.sub(replace, text).strip()
