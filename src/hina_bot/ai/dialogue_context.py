"""Provider-only dialogue attribution.

Capture, persistence, privacy policy, and observability still use original rows.
This projection preserves historical text verbatim while exposing who said what.
"""

from __future__ import annotations


def _historical_exchange(row: dict, current_user_id: str) -> dict:
    """Bind a stored server user/reply pair to its actual conversation owner."""
    result = {
        key: value for key, value in row.items()
        if key not in {"user", "hina"}
    }
    result["context_kind"] = "historical_exchange"
    result["user_message"] = {
        "role": "user",
        **({"author_user_id": current_user_id} if current_user_id else {}),
        "content": row["user"],
    }
    result["assistant_reply"] = {
        "role": "assistant",
        **({"reply_target_user_id": current_user_id} if current_user_id else {}),
        "content": row["hina"],
        **({"provenance_class": row["provenance_class"]}
           if row.get("provenance_class") == "reference_derived" else {}),
    }
    return result


def _project_dm_history(rows: list[dict], current_user_id: str) -> list[dict]:
    """Bundle only adjacent matching DM turn pairs; retain unpaired events."""
    history = []
    index = 0
    while index < len(rows):
        row = rows[index]
        reply = rows[index + 1] if index + 1 < len(rows) else None
        # Stored DM turns are emitted as adjacent user and assistant entries with
        # one turn timestamp. It describes the turn, NOT the assistant send time.
        pair = (
            row.get("role") == "user"
            and isinstance(reply, dict)
            and reply.get("role") == "assistant"
            and row.get("at") == reply.get("at")
        )
        if pair:
            shared = {
                key: value for key, value in row.items()
                if key not in {"role", "content"}
            }
            history.append({
                "context_kind": "historical_exchange",
                **shared,
                "user_message": {
                    "role": "user",
                    **({"author_user_id": current_user_id} if current_user_id else {}),
                    "content": row["content"],
                },
                "assistant_reply": {
                    "role": "assistant",
                    **({"reply_target_user_id": current_user_id} if current_user_id else {}),
                    "content": reply["content"],
                    **({"provenance_class": reply["provenance_class"]}
                       if reply.get("provenance_class") == "reference_derived" else {}),
                },
            })
            index += 2
            continue
        # Do not invent a pair if the input is incomplete or reordered.
        history.append({"context_kind": "historical_message", **row})
        index += 1
    return history


def project_historical_context(context: dict) -> dict:
    """Structure persisted conversations only for final model serialization."""
    projected = dict(context)
    current = str((context.get("current_speaker") or {}).get("user_id") or "")
    recent = context.get("personal_recent_conversation")
    if isinstance(recent, list):
        projected["personal_recent_conversation"] = [
            _historical_exchange(row, current)
            if isinstance(row, dict) and "user" in row and "hina" in row
            else row
            for row in recent
        ]
    history = context.get("conversation_history")
    if isinstance(history, list):
        projected["conversation_history"] = _project_dm_history(history, current)
    return projected


def _relation_to_current_speaker(row: dict, current_user_id: str) -> str:
    """Explicit ownership only; unknown assistant targets remain unknown."""
    role = str(row.get("role") or "")
    author = str(row.get("author_user_id") or row.get("user_id") or "")
    target = str(row.get("reply_target_user_id") or "")
    if role == "assistant":
        if not target:
            return "unknown_addressee"
        return (
            "hina_reply_to_current" if current_user_id and target == current_user_id
            else "hina_reply_to_other"
        )
    if role == "user":
        if not author:
            return "unknown_author"
        return (
            "current_author" if current_user_id and author == current_user_id
            else "other_author"
        )
    return "other_bot" if role == "bot" else "unknown_author"


def project_dialogue_context(context: dict) -> dict:
    """Preserve channel interleaving and enrich messages with grounded addressees.

    An explicit reply chain is a separate causal slice. Other channel rows keep
    the order selected by the collector (including explicit target history).
    No rows are filtered or reordered by this provider-only projection.
    """
    projected = project_historical_context(context)
    current = str((context.get("current_speaker") or {}).get("user_id") or "")
    rows = context.get("channel_recent_messages")
    if isinstance(rows, list):
        projected["channel_recent_messages"] = [
            {**row, "relation_to_current_speaker": _relation_to_current_speaker(row, current)}
            if isinstance(row, dict) else row
            for row in rows
        ]
    return projected
