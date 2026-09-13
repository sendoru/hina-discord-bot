"""Small authoritative self-profile facts that must not depend on public web lookup."""

_BIRTHDAY_REFERENCE = {
    "reference": "local_profile.hina.birthday",
    "kind": "world_fact",
    "content": "소라사키 히나의 생일은 2월 19일이다.",
    "awareness": "self",
    "time": "프로필 상시 설정",
}


def fallback_references(query: str) -> list[dict]:
    """Return stable profile facts missing from the main lexical lore index.

    This is intentionally tiny. The normal curated lore index remains the primary source; this
    fallback only prevents a stable self fact from silently becoming a web-search dependency.
    """
    if "생일" in query:
        return [dict(_BIRTHDAY_REFERENCE)]
    return []
