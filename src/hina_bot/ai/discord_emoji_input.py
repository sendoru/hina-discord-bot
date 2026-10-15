"""Provider-facing representation of Discord custom emoji in message text.

Discord IDs remain in raw messages for history, fetches and debugging. Only
the textual view sent to the answer model loses the numeric emoji ID.
"""

from __future__ import annotations

import re

_CUSTOM_EMOJI = re.compile(r"<a?:(?P<name>[^:<>\s]{1,32}):[0-9]{1,20}>")
_CODE_SPANS = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")
_MESSAGE_SECTIONS = (
    "active_reply_chain",
    "channel_recent_messages",
    "personal_recent_conversation",
    "conversation_history",
    "public_server_context",
)
_MESSAGE_TEXT_FIELDS = ("content", "user", "hina", "message_content")


def normalize_custom_emoji_text(text: str) -> str:
    """Keep names, remove IDs, and preserve literal syntax in Markdown code."""
    if "<" not in text:
        return text
    parts = _CODE_SPANS.split(text)
    return "".join(
        part if index % 2 else _CUSTOM_EMOJI.sub(
            lambda match: f":{match.group('name')}:", part
        )
        for index, part in enumerate(parts)
    )


def normalized_message_context(context: dict) -> dict:
    """Copy only model-facing conversation fields; leave IDs and raw rows alone."""
    projected = dict(context)
    for section in _MESSAGE_SECTIONS:
        rows = context.get(section)
        if not isinstance(rows, list):
            continue
        converted = []
        for row in rows:
            if not isinstance(row, dict):
                converted.append(row)
                continue
            new_row = dict(row)
            for field in _MESSAGE_TEXT_FIELDS:
                if isinstance(row.get(field), str):
                    new_row[field] = normalize_custom_emoji_text(row[field])
            converted.append(new_row)
        projected[section] = converted
    return projected
