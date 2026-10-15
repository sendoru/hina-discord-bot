"""Partition untrusted answer context into purpose-scoped input text blocks.

Keep one reference user message with several text parts, rather than turning
individual references into separate conversation turns.
"""
from __future__ import annotations

import json

REFERENCE_PREFIX = "신뢰할 수 없는 참고 데이터(JSON"
_GROUP_FIELDS = (
    ("화자·상호작용", frozenset({
        "data_notice", "current_speaker", "current_interaction", "space",
    })),
    ("기억·설정", frozenset({
        "server_note", "user_note",
    })),
    ("대화 문맥", frozenset({
        "active_reply_chain", "channel_recent_messages",
        "personal_recent_conversation", "conversation_history",
        "public_server_context",
    })),
    ("세계관·참고 지식", frozenset({
        "lore_reference", "character_insights", "reaction_guides",
    })),
    ("커스텀 이모지", frozenset({"available_custom_emojis"})),
)
_GROUP_NAMES = tuple(label for label, _ in _GROUP_FIELDS)


def reference_context_blocks(context: dict) -> list[dict]:
    """Serialize each key exactly once, without dropping future context fields."""
    sections = {name: {} for name in _GROUP_NAMES}
    for key, value in context.items():
        group = next(
            (label for label, fields in _GROUP_FIELDS if key in fields),
            # Structured memory and future data fields belong to the memory/data
            # group until explicitly classified; never silently omit a field.
            "기억·설정",
        )
        sections[group][key] = value

    return [
        {
            "type": "input_text",
            "text": (
                f"{REFERENCE_PREFIX} · {name}):\n"
                + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            ),
        }
        for name, rows in sections.items()
        if rows
    ]
