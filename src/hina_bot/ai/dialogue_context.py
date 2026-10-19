"""Project approved channel context into speaker-owned and ambient evidence.

This is a provider-input presentation boundary, not a capture, retention, or
privacy boundary. Egress filtering must have happened before this function.
Do not alter stored Discord rows or causal reply anchors.
"""

from __future__ import annotations

# A fresh participant needs to see the immediate scene, not an entire exchange
# between someone else and Hina. Explicit replies and same-speaker threads
# retain their existing, independently budgeted context.
_FRESH_SPEAKER_AMBIENT_ROWS = 8


def project_dialogue_context(
    context: dict, *, preserve_ambient: bool = False
) -> tuple[dict, dict]:
    """Separate direct conversational continuity from channel scene.

    Keep the original order and metadata of each row. A row is personal
    continuity only if speaker binding or its existing context kind proves it.
    Unknown-target assistant replies remain ambient, not personal.
    """
    speaker: list[dict] = []
    ambient: list[dict] = []
    target: list[dict] = []
    for row in context.get("channel_recent_messages", ()) or ():
        kind = str(row.get("context_kind") or "")
        if kind == "target_user_history":
            target.append(row)
        elif (
            kind == "speaker_thread"
            or row.get("is_current_speaker") is True
            or row.get("reply_target_is_current_speaker") is True
        ):
            speaker.append(row)
        else:
            ambient.append(row)

    original_ambient_count = len(ambient)
    has_active_reply = bool(context.get("active_reply_chain"))
    # Explicit replies can refer to a wider group conversation, and existing
    # direct speaker history must not be displaced. For a *fresh* speaker with
    # neither anchor, an oversized prior exchange is weak scene evidence.
    if not speaker and not has_active_reply and not preserve_ambient:
        ambient = ambient[-_FRESH_SPEAKER_AMBIENT_ROWS:]

    projected = dict(context)
    projected.pop("channel_recent_messages", None)
    projected["speaker_recent_messages"] = speaker
    projected["channel_ambient_messages"] = ambient
    projected["target_user_history"] = target
    projected["conversation_focus"] = {
        "current_speaker_in_recent_channel": bool(speaker),
        "explicit_reply_chain_present": has_active_reply,
        "ambient_is_other_participants_context": True,
    }
    return projected, {
        "speaker_rows": len(speaker),
        "ambient_rows_input": original_ambient_count,
        "ambient_rows_sent": len(ambient),
        "target_history_rows": len(target),
        "has_active_reply": has_active_reply,
    }
