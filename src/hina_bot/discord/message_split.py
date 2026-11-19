"""Model-guided Discord message splitting with deterministic Markdown-aware fallback.

The delimiter is a transport hint, not a trusted length or formatting guarantee.
Keep this module character-agnostic so other personalities can reuse it.
"""

from __future__ import annotations

import re

MESSAGE_SPLIT_DELIMITER = "<<<MESSAGE_SPLIT>>>"
MODEL_MESSAGE_TARGET_CHARS = 1800
DISCORD_MESSAGE_LIMIT = 1900  # Below Discord's 2,000 UTF-16 code-unit ceiling.

_DELIMITER_LINE = re.compile(r"^[ \t]*<<<MESSAGE_SPLIT>>>[ \t]*$")
_FENCE_LINE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_STYLES_OR_CODE = re.compile(r"(?<!\\)(`+|\*\*|__|~~|\|\|)")
_BOUNDARIES = re.compile(r"\n[ \t]*\n+|\n|[ \t]+")
_TOKENS = re.compile(r"<a?:[^:<>\s]{1,32}:\d{1,20}>|.", re.DOTALL)


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _fence_change(line: str, fence: tuple[str, str] | None):
    matched = _FENCE_LINE.match(line.rstrip("\r\n"))
    if not matched:
        return fence, False
    marks, info = matched.groups()
    if fence is None:
        # Markdown opening fences may include a language tag.
        return (marks, info), True
    if marks[0] == fence[0][0] and len(marks) >= len(fence[0]) and not info.strip():
        return None, True
    return fence, False


def _format_state(text: str):
    """Return open fenced code, inline code and paired Discord styling at text end."""
    fence = None
    inline = None
    styles: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        fence, consumed = _fence_change(line, fence)
        if consumed or fence is not None:
            continue
        for match in _STYLES_OR_CODE.finditer(line):
            token = match.group()
            if token.startswith("`"):
                if inline is None:
                    inline = token
                elif len(inline) == len(token):
                    inline = None
                continue
            if inline is not None:
                continue
            if styles and styles[-1] == token:
                styles.pop()
            elif token in styles:
                styles.remove(token)
            else:
                styles.append(token)
        # Discord inline code is line-local; an unfinished delimiter does not
        # protect unrelated lines from being treated as ordinary text.
        if raw_line.endswith(("\n", "\r")):
            inline = None
    return fence, inline, styles


def model_segments(text: str) -> list[str]:
    """Recognize only standalone, non-code delimiters; preserve literal code examples."""
    segments = []
    current: list[str] = []
    found = False
    for line in text.splitlines(keepends=True):
        if _DELIMITER_LINE.fullmatch(line.rstrip("\r\n")):
            fence, inline, styles = _format_state("".join(current))
            if fence is None and inline is None and not styles:
                found = True
                segment = "".join(current).strip("\r\n")
                if segment.strip():
                    segments.append(segment)
                current = []
                continue
            if fence is None:
                # A malformed boundary inside an emphasis span is not sent literally.
                found = True
                current.append("\n")
                continue
        current.append(line)
    if not found:
        return [text] if text else []
    segment = "".join(current).strip("\r\n")
    if segment.strip():
        segments.append(segment)
    return segments


def _fitting_index(text: str, budget: int) -> int:
    """Avoid cutting inside UTF-16 surrogate pairs or Discord custom emoji tokens."""
    used = 0
    end = 0
    for match in _TOKENS.finditer(text):
        size = utf16_length(match.group())
        if used + size > budget:
            break
        used += size
        end = match.end()
    return end


def _preferred_cut(text: str, max_end: int) -> int:
    """Prefer a full paragraph/line outside formatting, then whitespace, then hard cut."""
    candidates = []
    for match in _BOUNDARIES.finditer(text, 0, max_end):
        pos = match.end()
        if pos > max_end or pos < max(1, max_end // 3):
            continue
        fence, inline, styles = _format_state(text[:pos])
        is_newline = "\n" in match.group()
        is_paragraph = match.group().count("\n") >= 2
        if not fence and not inline and not styles:
            priority = 6 if is_paragraph else 5 if is_newline else 4
        elif is_newline:
            priority = 3 if fence else 2
        elif not fence and inline is None:
            priority = 1
        else:
            priority = 0
        if priority:
            candidates.append((priority, pos))
    if candidates:
        return max(candidates)[1]
    return max_end


def _fallback_split(text: str, limit: int) -> list[str]:
    if utf16_length(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    remaining = text
    while remaining and utf16_length(remaining) > limit:
        # Leave room for closing and reopening a code fence or styling span.
        reserve = min(96, max(16, limit // 10))
        end = _fitting_index(remaining, max(1, limit - reserve))
        if not end:
            end = _fitting_index(remaining, limit)
        if not end:
            # This cannot happen with practical Discord limits and valid Unicode.
            raise ValueError("Discord message limit cannot fit the next character")
        cut = _preferred_cut(remaining, end)
        prefix, rest = remaining[:cut], remaining[cut:]
        if not prefix:
            raise ValueError("Discord splitter could not make progress")
        fence, inline, styles = _format_state(prefix)
        body = prefix.rstrip("\r\n")
        if styles or inline:
            # A closing Markdown marker after whitespace may not close an
            # emphasis/code span in Discord; move that whitespace to the boundary.
            body = body.rstrip(" \t")
        suffix = "".join(reversed(styles))
        reopen = "".join(styles)
        if inline:
            suffix += inline
            reopen = inline + reopen
        if fence:
            marker, info = fence
            # Reuse the fence's marker and a short language tag when possible.
            language = (info.strip().split() or [""])[0][:32]
            suffix += "\n" + marker
            reopen += marker + (language if marker[0] == "`" else "") + "\n"
        rendered = body + suffix
        if utf16_length(rendered) > limit:
            # Extremely long or unusual formatting markers: safe delivery wins.
            from hina_bot.core.routing import chunks
            return parts + list(chunks(remaining, limit=limit))
        if rendered:
            parts.append(rendered)
        remaining = reopen + (rest if fence else rest.lstrip("\r\n"))
    if remaining:
        parts.append(remaining)
    return parts


def prepare_discord_answer(text: str, *, limit: int = DISCORD_MESSAGE_LIMIT) -> tuple[str, list[str]]:
    """Return delimiter-free history text and bounded Discord payloads.

    The history keeps the original generated content (aside from transport hints),
    while outbound chunks may add fence/style closures for valid independent Markdown.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    segments = model_segments(text)
    cleaned = "\n\n".join(segments) if len(segments) > 1 else (segments[0] if segments else "")
    parts = [part for segment in segments for part in _fallback_split(segment, limit)]
    return cleaned, parts
