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

        # Busy public channels can produce many short side messages between two turns of the same
        # conversation. Keep the caller's own thread as a first-class slice instead of letting
        # unrelated ambient chatter evict it from a small recency window. Cross-user assistant
        # replies are still excluded above, so this continuity does not reintroduce tone leakage.
        speaker_thread = []
        ambient = []
        for row in base:
            author_id = str(row.get("author_user_id") or row.get("user_id") or "")
            reply_target = str(row.get("reply_target_user_id") or "")
            if author_id == current_user_id or reply_target == current_user_id:
                item = dict(row)
                item["context_kind"] = "speaker_thread"
                item["reference_strength"] = "same_speaker"
                speaker_thread.append(item)
            else:
                item = dict(row)
                item["context_kind"] = "channel_ambient"
                ambient.append(item)

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

        # One character budget covers every source of channel context, but the item budget is now
        # slightly wider because short ambient Discord chatter should not erase an ongoing speaker
        # thread. Priority is: explicit reply, same-speaker continuity, ambient channel chat, then
        # target-user history. Unused reservations flow back to the remaining sources.
        remaining = max(0, int(self.budget))
        slots = 18

        replied_selected, remaining = self._take_recent(replied, remaining, slots)
        slots -= len(replied_selected)

        target_slots = min(3, slots) if extra else 0
        target_reserve = min(1500, remaining // 5) if extra else 0
        channel_budget = max(0, remaining - target_reserve)
        channel_slots = max(0, slots - target_slots)

        thread_slots = min(8, max(4, channel_slots // 2)) if speaker_thread else 0
        thread_reserve = min(3000, channel_budget // 2) if speaker_thread else 0
        thread_selected, thread_unused = self._take_recent(
            speaker_thread,
            thread_reserve,
            thread_slots,
        )
        thread_used = thread_reserve - thread_unused
        channel_budget -= thread_used
        channel_slots -= len(thread_selected)

        ambient_selected, ambient_unused = self._take_recent(
            ambient,
            channel_budget,
            channel_slots,
        )
        remaining = target_reserve + ambient_unused
        slots -= len(thread_selected) + len(ambient_selected)

        target_selected, target_unused = self._take_recent(
            extra,
            remaining,
            min(target_slots, slots),
        )
        remaining = target_unused
        slots -= len(target_selected)

        selected_base_ids = {
            str(row.get("message_id", ""))
            for row in thread_selected + ambient_selected
        }
        older_base = [
            row for row in speaker_thread + ambient
            if str(row.get("message_id", "")) not in selected_base_ids
        ]
        older_base.sort(key=lambda row: row.get("message_id", ""))
        base_backfill, remaining = self._take_recent(older_base, remaining, slots)

        selected_base = self._merge_in_source_order(
            speaker_thread + ambient,
            thread_selected,
            ambient_selected,
            base_backfill,
        )
        selected_base.sort(key=lambda row: row.get("message_id", ""))

        return target_selected + selected_base + replied_selected
