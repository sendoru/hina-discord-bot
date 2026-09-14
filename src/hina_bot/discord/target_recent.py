from contextvars import ContextVar

from .chatlog_capture import capture_mode
from .recent import RecentMessages
from .reply_context import REPLY_CONTEXT
from .target_context import TARGET_CONTEXT

CURRENT_DIRECT_TRIGGER = ContextVar("current_direct_trigger", default=False)


class TargetAwareRecentMessages(RecentMessages):
    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store

    def add(self, scope, message_id, name, content, *, role="user", unix_time=None):
        if (
            role != "assistant"
            and self.store is not None
            and capture_mode(self.store, scope) == "direct"
            and not CURRENT_DIRECT_TRIGGER.get()
        ):
            return
        super().add(
            scope,
            message_id,
            name,
            content,
            role=role,
            unix_time=unix_time,
        )

    def context(self, scope, before_id):
        base = super().context(scope, before_id)

        # Live assistant rows inherit the triggering user's scope, so their user_id identifies the
        # reply target. Hydrated bot-authored rows instead carry the bot author's id and therefore
        # have no trustworthy target. Keep an assistant reply only for the same current user; this
        # prevents one user's tense exchange from becoming the next user's default tone, and also
        # fails closed for untargeted assistant history recovered after a restart.
        current_user_id = str(scope.user_id)
        base = [
            row for row in base
            if row.get("role") != "assistant" or str(row.get("user_id", "")) == current_user_id
        ]

        replied = []
        reply_ids = set()
        for row in REPLY_CONTEXT.get():
            message_id = str(row.get("message_id", ""))
            item = dict(row)
            item["content"] = str(item.get("content", ""))[:4000]
            item["context_kind"] = "replied_message"
            item["reference_strength"] = "explicit_reply"
            replied.append(item)
            if message_id:
                reply_ids.add(message_id)

        if reply_ids:
            base = [
                row for row in base
                if str(row.get("message_id", "")) not in reply_ids
            ]

        seen = reply_ids | {
            str(row.get("message_id", "")) for row in base
            if row.get("message_id") is not None
        }
        extra = []
        for target in TARGET_CONTEXT.get():
            for sampled in target.get("sampled_messages", ()):
                message_id = str(sampled.get("message_id", ""))
                if message_id and message_id in seen:
                    continue
                extra.append({
                    "message_id": message_id,
                    "user_id": str(target.get("user_id", "")),
                    "name": str(target.get("name", ""))[:100],
                    "content": str(sampled.get("content", ""))[:2400],
                    "role": "user",
                    "context_kind": "target_user_history",
                    "at": str(sampled.get("at", "")),
                })
                if message_id:
                    seen.add(message_id)

        return extra + base + replied
