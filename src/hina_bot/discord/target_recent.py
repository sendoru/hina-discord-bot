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

    @staticmethod
    def _take_recent(rows, budget, slots):
        """Take the newest rows within a local budget and return them chronologically."""
        selected = []
        remaining = max(0, int(budget))
        slots = max(0, int(slots))
        for row in reversed(rows):
            if remaining <= 0 or len(selected) >= slots:
                break
            content = str(row.get("content", ""))
            if not content:
                continue
            item = dict(row)
            item["content"] = content[:remaining]
            remaining -= len(item["content"])
            selected.append(item)
        return list(reversed(selected)), remaining

    @staticmethod
    def _merge_in_source_order(source, *groups):
        selected = {}
        for group in groups:
            for row in group:
                selected[str(row.get("message_id", ""))] = row
        return [
            selected[str(row.get("message_id", ""))]
            for row in source
            if str(row.get("message_id", "")) in selected
        ]

    def context(self, scope, before_id):
        current_user_id = str(scope.user_id)

        def eligible_base(row):
            # Only assistant replies explicitly targeted at the current user can carry relationship
            # tone forward. Apply this before any item/character budget so discarded replies do not
            # crowd out older but still useful channel messages.
            return (
                row.get("role") != "assistant"
                or str(row.get("reply_target_user_id") or "") == current_user_id
            )

        base = super().candidates(scope, before_id, include=eligible_base)

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
                    "author_user_id": str(target.get("user_id", "")),
                    "reply_target_user_id": None,
                    "name": str(target.get("name", ""))[:100],
                    "content": str(sampled.get("content", ""))[:2400],
                    "role": "user",
                    "context_kind": "target_user_history",
                    "at": str(sampled.get("at", "")),
                })
                if message_id:
                    seen.add(message_id)

        # One budget now covers every source of channel context. Explicit replies have the highest
        # priority, normal recent chat comes next, and target-user history receives only a small
        # reserved slice so it cannot overwhelm the current conversation. Unused space is returned
        # to normal recent chat.
        remaining = max(0, int(self.budget))
        slots = 12

        replied_selected, unused = self._take_recent(replied, remaining, slots)
        remaining = unused
        slots -= len(replied_selected)

        target_slots = min(3, slots) if extra else 0
        target_reserve = min(1800, remaining // 4) if extra else 0
        base_slots = max(0, slots - target_slots)
        base_selected, base_unused = self._take_recent(
            base,
            max(0, remaining - target_reserve),
            base_slots,
        )
        remaining = target_reserve + base_unused
        slots -= len(base_selected)

        target_selected, target_unused = self._take_recent(
            extra,
            remaining,
            min(target_slots, slots),
        )
        remaining = target_unused
        slots -= len(target_selected)

        selected_base_ids = {
            str(row.get("message_id", "")) for row in base_selected
        }
        older_base = [
            row for row in base
            if str(row.get("message_id", "")) not in selected_base_ids
        ]
        base_backfill, remaining = self._take_recent(older_base, remaining, slots)
        base_selected = self._merge_in_source_order(base, base_selected, base_backfill)

        return target_selected + base_selected + replied_selected
