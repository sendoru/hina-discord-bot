"""Provider-only speaker attribution for recent Discord conversation.

This does not filter messages, infer topical relevance, or mutate captured turns.
It simply prevents the model from reading unrelated speakers as one conversation.
"""

from __future__ import annotations


def project_dialogue_context(context: dict) -> dict:
    """Separate current-speaker exchanges from other participants at final egress."""
    projected = dict(context)
    current = str((context.get("current_speaker") or {}).get("user_id") or "")
    speaker_rows: list[dict] = []
    ambient_rows: list[dict] = []

    for row in context.get("channel_recent_messages") or ():
        role = str(row.get("role") or "")
        author = str(row.get("author_user_id") or row.get("user_id") or "")
        target = str(row.get("reply_target_user_id") or "")
        # Assistant turns belong to their actual addressee, not the bot's own author ID.
        owned = bool(current) and (
            (role == "user" and author == current)
            or (role == "assistant" and target == current)
        )
        (speaker_rows if owned else ambient_rows).append(row)

    projected.pop("channel_recent_messages", None)
    projected["speaker_recent_messages"] = speaker_rows
    projected["channel_ambient_messages"] = ambient_rows
    projected["conversation_focus"] = {
        "current_speaker_in_recent_channel": bool(speaker_rows),
        "explicit_reply_chain_present": bool(context.get("active_reply_chain")),
        "ambient_is_other_participants_context": True,
    }
    return projected
