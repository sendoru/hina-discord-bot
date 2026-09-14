"""Helpers for contextual follow-up routing."""

import re

_FOLLOWUP = re.compile(r"^\s*(?:그럼|그러면|걔|그거|거기)", re.IGNORECASE)


def is_followup(content: str) -> bool:
    return bool(_FOLLOWUP.search(content.strip()))


def join_followup(anchor: str, content: str) -> str:
    if not anchor:
        return content
    return anchor + "\n" + content
