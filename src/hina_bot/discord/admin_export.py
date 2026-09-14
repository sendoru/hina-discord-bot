"""Helpers for exporting verbose admin data as Discord attachments."""

import io

import discord


def text_attachment(content: str, filename: str) -> discord.File:
    """Build an in-memory UTF-8 text attachment for an ephemeral admin response."""
    payload = io.BytesIO(content.encode("utf-8"))
    return discord.File(payload, filename=filename)


__all__ = ["text_attachment"]
