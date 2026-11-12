"""Provider-facing conversation input modes (#506).

Selection, egress privacy and history/session cutoffs are applied before this layer.
Synthetic DM replay is not an original Gemini Interaction transcript.
"""
from __future__ import annotations

from .dialogue_context import project_dialogue_context
from .reference_context_blocks import reference_context_blocks

_CURRENT_FIELDS = ("current_speaker", "current_interaction", "active_reply_chain")
_MODES = frozenset({"legacy", "structured", "structured_with_replay"})


def _replayable_exchange(row: dict) -> bool:
    """Only complete text-only stored exchanges may be reconstructed as role steps.

    An answer derived from an explicit history lookup is reference material; it must
    not acquire the authority of a prior model step. Unknown/incomplete rows stay
    in the reference envelope. Gemini thoughts/tool calls are NOT reconstructed.
    """
    if not isinstance(row, dict) or row.get("context_kind") != "historical_exchange":
        return False
    user = row.get("user_message")
    assistant = row.get("assistant_reply")
    return (
        isinstance(user, dict)
        and isinstance(assistant, dict)
        and user.get("role") == "user"
        and assistant.get("role") == "assistant"
        and isinstance(user.get("content"), str)
        and bool(user["content"])
        and isinstance(assistant.get("content"), str)
        and bool(assistant["content"])
        and row.get("provenance_class") != "reference_derived"
        and assistant.get("provenance_class") != "reference_derived"
    )


def _replay_suffix(background: dict) -> list[dict]:
    """Replay a complete contiguous suffix, retaining earlier material as references."""
    history = background.get("conversation_history")
    if not isinstance(history, list):
        return []
    start = len(history)
    while start and _replayable_exchange(history[start - 1]):
        start -= 1
    if start == len(history):
        return []

    replay = history[start:]
    background["conversation_history"] = history[:start]
    steps = []
    for exchange in replay:
        steps.extend((
            {"role": "user", "content": exchange["user_message"]["content"]},
            {"role": "assistant", "content": exchange["assistant_reply"]["content"]},
        ))
    return steps


def build_context_input(
    context: dict,
    current_content: str,
    *,
    mode: str = "legacy",
    provider: str = "gemini",
    is_dm: bool = False,
) -> list[dict]:
    """Serialize already-authorized context; never retrieve or select new rows."""
    if mode not in _MODES:
        raise ValueError(f"Unsupported context input mode: {mode}")

    projected = project_dialogue_context(context)
    if mode == "legacy":
        return [
            {"role": "user", "content": reference_context_blocks(projected)},
            {"role": "user", "content": current_content},
        ]

    background = dict(projected)
    current = {
        key: background.pop(key)
        for key in _CURRENT_FIELDS if key in background
    }

    # Do not synthesize server participants or replay an unknown model/tool history.
    # Only Gemini DM explicitly opts into text-only synthetic role steps.
    replay = (
        _replay_suffix(background)
        if mode == "structured_with_replay" and provider == "gemini" and is_dm
        else []
    )

    # Preserve the existing untrusted-reference boundary. Only the position of
    # the trusted Discord interaction metadata and direct reply chain changes.
    current_parts = reference_context_blocks(current)
    current_parts.append({"type": "input_text", "text": current_content})
    return [
        {"role": "user", "content": reference_context_blocks(background)},
        *replay,
        {"role": "user", "content": current_parts},
    ]
