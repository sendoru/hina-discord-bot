import json
import re

_DOMAIN_CITATION = re.compile(r"\s*\((?:www\.)?(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s)]*)?\)", re.IGNORECASE)
_CONTROL_KEYS = {
    "calculator",
    "response_length",
    "search_query",
    "open",
    "click",
    "find",
    "image_query",
    "product_query",
    "businesses_query",
    "web_search",
    "tool_choice",
}
_ACTION_KEYS = _CONTROL_KEYS - {"response_length", "tool_choice"}


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _remove_annotation_spans(text: str, annotations) -> str:
    spans = []
    for annotation in annotations or []:
        if _field(annotation, "type") != "url_citation":
            continue
        start = _field(annotation, "start_index")
        end = _field(annotation, "end_index")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
            spans.append((start, end))
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text


def _looks_like_internal_control(value) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    keys = set(value)
    if not keys <= _CONTROL_KEYS:
        return False
    return bool(keys & _ACTION_KEYS) and (
        "response_length" in keys or "tool_choice" in keys or len(keys) == 1
    )


def strip_internal_control_prefix(text: str) -> str:
    """Drop accidental serialized tool/control objects from the start of visible output."""
    decoder = json.JSONDecoder()
    rest = text.lstrip()
    while rest.startswith("{"):
        try:
            value, end = decoder.raw_decode(rest)
        except json.JSONDecodeError:
            break
        if not _looks_like_internal_control(value):
            break
        rest = rest[end:].lstrip()
    return rest


def response_text(response, *, hide_citations: bool = False) -> str:
    fallback = (_field(response, "output_text", "") or "").strip()
    if not hide_citations:
        return strip_internal_control_prefix(fallback).strip()

    pieces = []
    for item in _field(response, "output", []) or []:
        if _field(item, "type") != "message":
            continue
        for part in _field(item, "content", []) or []:
            if _field(part, "type") != "output_text":
                continue
            text = _field(part, "text", "") or ""
            text = _remove_annotation_spans(text, _field(part, "annotations", []))
            pieces.append(text)

    text = "".join(pieces).strip() if pieces else fallback
    text = strip_internal_control_prefix(text)
    text = re.sub(r"[ \t]+([,.!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
