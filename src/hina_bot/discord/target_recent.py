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

    def add(
        self,
        scope,
        message_id,
        name,
        content,
        *,
        role="user",
        unix_time=None,
        author_user_id=None,
        reply_target_user_id=None,
    ):
        if (
            role != "assistant"
            and self.store is not None
            and capture_mode(self.store, scope) == "direct"
            and not CURRENT_DIRECT_TRIGGER.get()
        ):
            return

        # Existing callers use the triggering user's scope for live assistant replies, while
        # Discord history hydration uses the bot author's scope and supplies an original timestamp.
        # Translate those two call shapes into explicit metadata instead of overloading `user_id`.
        if role == "assistant" and author_user_id is None and reply_target_user_id is None:
            if unix_time is None:
                reply_target_user_id = scope.user_id
            else:
                author_user_id = scope.user_id

        super().add(
            scope,
            message_id,
            name,
            content,
            role=role,
            unix_time=unix_time,
            author_user_id=author_user_id,
            reply_target_user_id=reply_target_user_id,
        )

    def context(self, scope, before_id):
        base = super().context(scope, before_id)

        # Only assistant replies explicitly targeted at the current user can carry relationship
        # tone forward. Hydrated bot-authored rows have no trustworthy target and fail closed.
        current_user_id = str(scope.user_id)
        base = [
            row for row in base
            if row.get("role") != "assistant"
            or str(row.get("reply_target_user_id") or "") == current_user_id
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
