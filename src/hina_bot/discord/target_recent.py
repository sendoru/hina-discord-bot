from contextvars import ContextVar

from ..ai.egress_policy import filter_channel_context
from .chatlog_capture import capture_mode
from .recent import RecentMessages
from .reply_context import REPLY_CONTEXT
from .target_context import TARGET_CONTEXT

CURRENT_DIRECT_TRIGGER = ContextVar("current_direct_trigger", default=False)


class TargetAwareRecentMessages(RecentMessages):
    def __init__(self, *args, store=None, external_context_policy="full", **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.external_context_policy = external_context_policy

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
        direct_trigger=None,
    ):
        direct = direct_trigger
        if role != "assistant" and direct is None:
            direct = bool(CURRENT_DIRECT_TRIGGER.get())
        if (
            role != "assistant"
            and self.store is not None
            and capture_mode(self.store, scope) == "direct"
            and not direct
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
            direct_trigger=direct,
        )
        # Delivered answers own ephemeral sources; buffer eviction/deletion removes both.
        if role == "assistant" and unix_time is None:
            for row in self.buffers.get(self._key(scope), ()):
                if row["message_id"] == message_id:
                    row["reply_sources"] = [dict(source) for source in REPLY_CONTEXT.get()][:1]
                    break

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
            if len(item["content"]) < len(content):
                item["truncated"] = True
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
        base = super().candidates(scope, before_id)

        explicit_ids = {str(row.get("message_id", "")) for row in REPLY_CONTEXT.get()}
        turns = [row for row in base if row.get("role") == "assistant" and (
            str(row.get("reply_target_user_id")) == current_user_id
            or str(row.get("message_id")) in explicit_ids
        )][-4:]
        sources = []
        seen_sources = set()
        for turn in reversed(turns):
            for source in turn.get("reply_sources", ()):
                source_id = str(source.get("message_id", ""))
                if source_id in seen_sources or source_id in explicit_ids:
                    continue
                seen_sources.add(source_id)
                item = dict(source)
                item.pop("reply_sources", None)
                item.update(context_kind="prior_reply_source",
                            reference_strength="prior_explicit_reply",
                            source_turn_message_id=str(turn["message_id"]),
                            source_turn_user_id=str(turn.get("reply_target_user_id", "")))
                sources.append(item)
        sources = list(reversed(sources[:2]))
        sources = filter_channel_context(sources, scope.user_id, self.external_context_policy)
        # Flatten before budgeting/filtering; nested data must never bypass egress policy.
        for row in base:
            row.pop("reply_sources", None)

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
        # unrelated ambient chatter evict it from a small recency window. Cross-user Hina replies
        # remain available as channel context; explicit reply-target metadata keeps their tone
        # attributable to the user and situation that caused it.
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

        seen = set(reply_ids)
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
                    "direct_trigger": sampled.get("direct_trigger"),
                    "target_retrieval_mode": str(target.get("retrieval_mode", "")),
                    "explicit_history_request": bool(
                        target.get("explicit_history_request", False)
                    ),
                    "name": str(target.get("name", ""))[:100],
                    "content": str(sampled.get("content", ""))[:2400],
                    "role": "user",
                    "context_kind": "target_user_history",
                    "at": str(sampled.get("at", "")),
                })
                if message_id:
                    seen.add(message_id)

        # A freshly sampled target row is more specific than the same message's passive recent
        # copy. Keep one copy with target provenance and target-budget priority.
        extra_ids = {
            str(row.get("message_id", "")) for row in extra
            if row.get("message_id") is not None
        }
        speaker_thread = [
            row for row in speaker_thread
            if str(row.get("message_id", "")) not in extra_ids
        ]
        ambient = [
            row for row in ambient
            if str(row.get("message_id", "")) not in extra_ids
        ]

        # One character budget covers every source of channel context, but the item budget is now
        # slightly wider because short ambient Discord chatter should not erase an ongoing speaker
        # thread. Priority is: explicit reply, same-speaker continuity, ambient channel chat, then
        # target-user history. Unused reservations flow back to the remaining sources.
        remaining = max(0, int(self.budget))
        slots = 18

        replied_selected, remaining = self._take_recent(replied, remaining, slots)
        slots -= len(replied_selected)

        source_selected, remaining = self._take_recent(sources, remaining, min(2, slots))
        slots -= len(source_selected)

        deep_target = any(row.get("target_retrieval_mode") == "deep" for row in extra)
        target_slots = min(8 if deep_target else 3, slots) if extra else 0
        target_reserve = min(2400 if deep_target else 1200, remaining // 3) if extra else 0
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

        return target_selected + selected_base + source_selected + replied_selected
