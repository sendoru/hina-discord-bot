"""Image type detection from file signatures, shared by core, ai and discord code."""

from __future__ import annotations


def sniff_image_mime(data: bytes) -> str | None:
    """Return the MIME type of a supported image by its bytes, never by file name."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


__all__ = ["sniff_image_mime"]
