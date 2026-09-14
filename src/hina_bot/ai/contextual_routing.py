"""Helpers for contextual follow-up routing."""


def join_followup(anchor: str, content: str) -> str:
    if not anchor:
        return content
    return anchor + "\n" + content
