"""Text projection for Discord forwards used by the recent-message context.

A forward's original message is a snapshot, not the sending message's `content`.
The snapshot intentionally has no original author identity; keep that boundary
explicit instead of attributing its text to the forwarding user or to Hina.
"""

from __future__ import annotations


def message_context_text(message) -> str:
    """Combine the sender's own text with bounded, clearly attributed forward text."""
    content = str(getattr(message, "content", "") or "")
    forwarded = []
    for snapshot in (getattr(message, "message_snapshots", None) or ())[:2]:
        text = str(getattr(snapshot, "content", "") or "")
        if text.strip():
            forwarded.append(
                "[Discord 전달 메시지 (Forward; 원 작성자 확인 불가)]\n"
                + text
            )

    if not forwarded:
        return content
    return "\n\n".join(([content] if content.strip() else []) + forwarded)
