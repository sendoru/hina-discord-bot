"""Text projection for Discord forwards used by the recent-message context.

A forward's original message is a snapshot, not the sending message's `content`.
The snapshot intentionally has no original author identity; keep that boundary
explicit instead of attributing its text to the forwarding user or to Hina.
"""

from __future__ import annotations

from .audio import audio_context_text
from .embed_context import message_embed_text


def message_forward_text(message) -> str:
    """Project the visible Forward snapshots without inventing an original author."""
    forwarded = []
    for snapshot in (getattr(message, "message_snapshots", None) or ())[:2]:
        text = str(getattr(snapshot, "content", "") or "")
        if text.strip():
            forwarded.append(
                "[Discord 전달 메시지 (Forward; 원 작성자 확인 불가)]\n"
                + text
            )
    return "\n\n".join(forwarded)


def message_turn_text(message, routed_text: str) -> str:
    """Append forwarded evidence *after* deciding whether this message calls Hina.

    Only the message's own content/mentions may trigger a reply. A snapshot must
    never supply a prefix or mention to the routing decision.
    """
    return "\n\n".join(
        part for part in (routed_text, message_forward_text(message)) if part.strip()
    )


def message_context_text(message) -> str:
    """Combine sender text, Forward snapshots, and Discord link previews."""
    content = str(getattr(message, "content", "") or "")
    forwarded = message_forward_text(message)
    preview = message_embed_text(message)
    audio = audio_context_text(message)
    return "\n\n".join(
        part for part in (content, forwarded, preview, audio) if part.strip()
    )
