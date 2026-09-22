from __future__ import annotations


def search_fragment(
    value: object,
    query: str,
    *,
    radius: int = 80,
) -> dict[str, str] | None:
    text = str(value or "")
    needle = (query or "").strip()
    if not text or not needle:
        return None

    index = text.lower().find(needle.lower())
    if index < 0:
        return None

    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    return {
        "before": ("…" if start else "") + text[start:index],
        "match": text[index:index + len(needle)],
        "after": text[index + len(needle):end] + ("…" if end < len(text) else ""),
    }


def search_matches(
    query: str,
    fields: tuple[tuple[str, object], ...],
) -> tuple[dict[str, object], ...]:
    needle = (query or "").strip()
    if not needle:
        return ()

    matches = []
    for label, value in fields:
        fragment = search_fragment(value, needle)
        if fragment is not None:
            matches.append({"field": label, "fragment": fragment})
    return tuple(matches)


__all__ = ["search_fragment", "search_matches"]
