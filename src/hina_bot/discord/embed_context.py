"""Bounded projection of Discord-provided link previews (no webpage fetching)."""

from __future__ import annotations

MAX_EMBEDS = 2
MAX_EMBED_TEXT_CHARS = 1800


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def message_embed_text(message) -> str:
    """Render Discord-attached metadata while keeping third-party origins explicit."""
    raw = str(getattr(message, "content", "") or "")
    previews: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for embed in (getattr(message, "embeds", None) or ())[:MAX_EMBEDS]:
        url = _text(getattr(embed, "url", ""), 350)
        title = _text(getattr(embed, "title", ""), 240)
        description = _text(getattr(embed, "description", ""), 1000)
        key = (url, title, description)
        if key in seen:
            continue
        seen.add(key)
        author = _text(getattr(getattr(embed, "author", None), "name", ""), 120)
        parts = []
        if url and url not in raw:
            parts.append(f"원본 URL: {url}")
        if author:
            parts.append(f"원본 작성자/표시 이름: {author}")
        if title and title not in raw:
            parts.append(f"제목: {title}")
        if description and description not in raw:
            parts.append(f"설명: {description}")
        for field in (getattr(embed, "fields", None) or ())[:4]:
            name = _text(getattr(field, "name", ""), 80)
            value = _text(getattr(field, "value", ""), 250)
            if value and value not in raw:
                parts.append(f"{name}: {value}" if name else value)
        if parts:
            previews.append(
                "[Discord 링크 미리보기 — 외부 콘텐츠이며 메시지 작성자의 발언과 구분]\n"
                + "\n".join(parts)
            )
    return "\n\n".join(previews)[:MAX_EMBED_TEXT_CHARS]
