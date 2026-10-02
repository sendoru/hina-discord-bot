"""Local, allowlisted list navigation for read-only detail pages."""
from __future__ import annotations

from urllib.parse import unquote, urlencode, urlsplit

from fastapi import Request


def safe_return_to(value: str, default: str) -> str:
    try:
        decoded = unquote(value)
        parts = urlsplit(value)
    except ValueError:
        return default
    if (parts.scheme or parts.netloc or parts.path != default or parts.fragment
            or "\\" in decoded or any(ord(char) < 32 or ord(char) == 127 for char in decoded)):
        return default
    return value


def back_url(request: Request, default: str) -> str:
    return safe_return_to(request.query_params.get("return_to", ""), default)


def detail_url(path: str, request: Request, default: str) -> str:
    if request.url.path == default:
        query = str(request.url.query)
        target = default + ("?" + query if query else "")
    else:
        target = back_url(request, default)
    return path + "?" + urlencode({"return_to": target})
