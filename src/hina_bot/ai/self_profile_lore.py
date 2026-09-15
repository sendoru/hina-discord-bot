"""Small authoritative self-profile facts that must not depend on public web lookup."""

from hina_bot.core.character import get_character_config


def fallback_references(query: str) -> list[dict]:
    """Return stable profile facts missing from the main lexical lore index.

    This is intentionally tiny. The normal curated lore index remains the primary source; this
    fallback only prevents a stable self fact from silently becoming a web-search dependency.
    """
    character = get_character_config()
    if "생일" not in query or not character.birthday:
        return []
    return [{
        "reference": "local_profile.character.birthday",
        "kind": "world_fact",
        "content": f"{character.name}의 생일은 {character.birthday}이다.",
        "awareness": "self",
        "time": "프로필 상시 설정",
    }]
