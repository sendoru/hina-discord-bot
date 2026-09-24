import time
from contextvars import ContextVar

from hina_bot.core.recent import RecentMessages

from ..ai.egress_policy import filter_channel_context
from .chatlog_capture import capture_mode
from .reply_context import REPLY_CONTEXT
from .target_context import TARGET_CONTEXT
from .turn_provenance import CURRENT_TURN_PROVENANCE

CURRENT_DIRECT_TRIGGER = ContextVar("current_direct_trigger", default=False)

_MAX_REFERENCE_SOURCES = 2


def _author_id(row: dict) -> str:
    return str(row.get("author_user_id") or row.get("user_id") or "")


def _is_reference_material(row: dict, current_user_id: int | str) -> bool:
    if row.get("provenance_class") == "reference_material":
        return True
    role = str(row.get("role") or "")
    return role not in {"assistant"} and _author_id(row) != str(current_user_id)


def _reference_sources(provenance: dict, current_user_id: int | str) -> list[dict]:
    """Return bounded original reference material, never assistant paraphrases."""

    selected: list[dict] = []
    seen: set[str] = set()
    rows = list(provenance.get("reference_sources", ()))
    rows.extend(provenance.get("origin_sources", ()))
    for raw in rows:
        row = dict(raw)
        source_id = str(row.get("message_id") or "")
        if not source_id or source_id in seen:
            continue
        if not _is_reference_material(row, current_user_id):
            continue
        row["provenance_class"] = "reference_material"
        selected.append(row)
        seen.add(source_id)
        if len(selected) >= _MAX_REFERENCE_SOURCES:
            break
    return selected


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
        capture_turn_provenance=False,
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

        # Live capture and Discord-history hydration must use the same row contract.
        # Callers provide assistant author/target identity explicitly; timestamps never imply
        # whether a row is live or hydrated.
        inherited_turn = (
            self._explicit_assistant_turn(scope, REPLY_CONTEXT.get())
            if role == "assistant" and capture_turn_provenance
            else None
        )

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
            capture_turn_provenance=capture_turn_provenance,
        )
        # Delivered live answers own ephemeral sources; buffer eviction/deletion removes both.
        if role == "assistant" and capture_turn_provenance:
            for row in self.buffers.get(self._key(scope), ()):
                if row["message_id"] == message_id:
                    row["reply_sources"] = [dict(source) for source in REPLY_CONTEXT.get()][:1]
                    provenance = CURRENT_TURN_PROVENANCE.get()
                    if provenance:
                        reference_sources = _reference_sources(provenance, scope.user_id)
                        if inherited_turn is not None:
                            inherited = inherited_turn.get("turn_provenance", {})
                            inherited_refs = _reference_sources(inherited, scope.user_id)
                            seen = {
                                str(source.get("message_id") or "")
                                for source in reference_sources
                            }
                            for source in inherited_refs:
                                source_id = str(source.get("message_id") or "")
                                if not source_id or source_id in seen:
                                    continue
                                reference_sources.append(dict(source))
                                seen.add(source_id)
                                if len(reference_sources) >= _MAX_REFERENCE_SOURCES:
                                    break
                        bounded_references = reference_sources[:_MAX_REFERENCE_SOURCES]
                        row["turn_provenance"] = {
                            "origin_request": dict(provenance.get("origin_request", {})),
                            "origin_sources": [
                                dict(source)
                                for source in provenance.get("origin_sources", ())
                            ][:2],
                            "reference_sources": bounded_references,
                        }
                        if bounded_references:
                            row["provenance_class"] = "reference_derived"
                            row["reference_source_ids"] = [
                                str(source.get("message_id") or "")
                                for source in bounded_references
                                if source.get("message_id")
                            ]
                            row["reference_source_author_ids"] = [
                                _author_id(source)
                                for source in bounded_references
                                if _author_id(source)
                            ]
                    break

    def _explicit_assistant_turn(self, scope, replied):
        current_user_id = str(scope.user_id)
        reply_ids = {
            str(row.get("message_id", ""))
            for row in replied
            if row.get("role") == "assistant"
        }
        if not reply_ids:
            return None
        self.prune(time.monotonic())
        for row in reversed(self.buffers.get(self._key(scope), ())):
            if str(row.get("message_id", "")) not in reply_ids:
                continue
            if str(row.get("reply_target_user_id") or "") != current_user_id:
                return None
            if row.get("turn_provenance"):
                return row
        return None

    def reply_chain_visual_ids(self, scope, replied) -> tuple[str, ...]:
        """Return only strong visual source IDs from the explicitly replied assistant turn."""
        turn = self._explicit_assistant_turn(scope, replied)
        if turn is None:
            return ()
        provenance = turn.get("turn_provenance", {})
        rows = [provenance.get("origin_request", {})]
        rows.extend(provenance.get("origin_sources", ()))
        rows.extend(provenance.get("reference_sources", ()))
        return tuple(
            str(row.get("message_id", ""))
            for row in rows
            if row.get("has_visual") and row.get("message_id")
        )[:3]

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
            if not content and not row.get("has_visual"):
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

    @classmethod
    def _take_active_reply_bundle(cls, active_chain, replied, budget, slots):
        """Keep explicit-reply anchors before spending context on weaker recent rows."""
        bundle = [*active_chain, *replied]
        remaining = max(0, int(budget))
        slots = max(0, int(slots))
        if not bundle or remaining <= 0 or slots <= 0:
            return [], remaining

        required_kinds = {"reply_origin_request", "replied_message"}
        required = [
            row for row in bundle
            if row.get("context_kind") in required_kinds
        ][:slots]
        selected_required = []
        for index, row in enumerate(required):
            if remaining <= 0:
                break
            anchors_left = len(required) - index
            share = max(1, remaining // anchors_left)
            content = str(row.get("content", ""))
            if not content and not row.get("has_visual"):
                continue
            item = dict(row)
            item["content"] = content[:share]
            if len(item["content"]) < len(content):
                item["truncated"] = True
            remaining -= len(item["content"])
            selected_required.append(item)

        selected_ids = {
            str(row.get("message_id", ""))
            for row in selected_required
            if row.get("message_id") is not None
        }
        optional = [
            row for row in bundle
            if (
                row.get("context_kind") not in required_kinds
                and str(row.get("message_id", "")) not in selected_ids
            )
        ]
        optional_selected, remaining = cls._take_recent(
            optional,
            remaining,
            max(0, slots - len(selected_required)),
        )
        return (
            cls._merge_in_source_order(
                bundle,
                optional_selected,
                selected_required,
            ),
            remaining,
        )

    def context(self, scope, before_id):
        current_user_id = str(scope.user_id)
        base = super().candidates(scope, before_id)

        active_turn = self._explicit_assistant_turn(scope, REPLY_CONTEXT.get())
        active_chain = []
        if active_turn is not None:
            provenance = active_turn.get("turn_provenance", {})
            chain_ids: set[str] = set()
            for source in provenance.get("reference_sources", ()):
                item = dict(source)
                source_id = str(item.get("message_id") or "")
                if source_id and source_id in chain_ids:
                    continue
                item.update(
                    context_kind="reply_reference_source",
                    reference_strength="inherited_reference",
                    provenance_class="reference_material",
                    source_turn_message_id=str(active_turn["message_id"]),
                )
                active_chain.append(item)
                if source_id:
                    chain_ids.add(source_id)
            for source in provenance.get("origin_sources", ()):
                item = dict(source)
                source_id = str(item.get("message_id") or "")
                if source_id and source_id in chain_ids:
                    continue
                item.update(
                    context_kind="reply_origin_source",
                    reference_strength="prior_explicit_reply",
                    provenance_class=(
                        "reference_material"
                        if _is_reference_material(item, scope.user_id)
                        else "conversation"
                    ),
                    source_turn_message_id=str(active_turn["message_id"]),
                )
                active_chain.append(item)
                if source_id:
                    chain_ids.add(source_id)
            request = provenance.get("origin_request")
            if request:
                item = dict(request)
                item.update(
                    context_kind="reply_origin_request",
                    reference_strength="prior_user_request",
                    provenance_class="conversation",
                    source_turn_message_id=str(active_turn["message_id"]),
                )
                active_chain.append(item)
            active_chain = filter_channel_context(
                active_chain,
                scope.user_id,
                self.external_context_policy,
            )

        explicit_ids = {str(row.get("message_id", "")) for row in REPLY_CONTEXT.get()}
        turns = [row for row in base if row.get("role") == "assistant" and (
            str(row.get("reply_target_user_id")) == current_user_id
            or str(row.get("message_id")) in explicit_ids
        )][-1:]
        sources = []
        seen_sources = set()
        for turn in reversed(turns):
            provenance = turn.get("turn_provenance", {})
            for source in _reference_sources(provenance, scope.user_id):
                source_id = str(source.get("message_id", ""))
                if source_id in seen_sources or source_id in explicit_ids:
                    continue
                seen_sources.add(source_id)
                item = dict(source)
                item.update(
                    context_kind="prior_reply_source",
                    reference_strength="prior_reference_material",
                    provenance_class="reference_material",
                    source_turn_message_id=str(turn["message_id"]),
                    source_turn_user_id=str(turn.get("reply_target_user_id", "")),
                )
                sources.append(item)
        sources = list(reversed(sources[:2]))
        sources = filter_channel_context(sources, scope.user_id, self.external_context_policy)
        # Flatten before budgeting/filtering; nested data must never bypass egress policy.
        for row in base:
            row.pop("reply_sources", None)
            row.pop("turn_provenance", None)

        replied = []
        reply_ids = set()
        for row in REPLY_CONTEXT.get():
            message_id = str(row.get("message_id", ""))
            item = dict(row)
            item["content"] = str(item.get("content", ""))[:4000]
            item["context_kind"] = "replied_message"
            item["reference_strength"] = "explicit_reply"
            if (
                active_turn is not None
                and str(active_turn.get("message_id") or "") == message_id
                and active_turn.get("provenance_class") == "reference_derived"
            ):
                item["provenance_class"] = "reference_derived"
                item["reference_source_ids"] = list(
                    active_turn.get("reference_source_ids", ())
                )
                item["reference_source_author_ids"] = list(
                    active_turn.get("reference_source_author_ids", ())
                )
            else:
                item["provenance_class"] = (
                    "conversation"
                    if item.get("role") == "assistant"
                    or _author_id(item) == current_user_id
                    else "reference_material"
                )
            replied.append(item)
            if message_id:
                reply_ids.add(message_id)

        if reply_ids:
            base = [
                row for row in base
                if str(row.get("message_id", "")) not in reply_ids
            ]

        chain_ids = {
            str(row.get("message_id", ""))
            for row in active_chain
            if row.get("message_id")
        }
        if chain_ids:
            base = [
                row for row in base
                if str(row.get("message_id", "")) not in chain_ids
            ]
            sources = [
                row for row in sources
                if str(row.get("message_id", "")) not in chain_ids
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
                item.setdefault("provenance_class", "conversation")
                speaker_thread.append(item)
            else:
                item = dict(row)
                item["context_kind"] = "channel_ambient"
                item.setdefault("provenance_class", "ambient")
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
                    "provenance_class": "reference_material",
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

        # One character budget covers every source of channel context. Explicit reply context
        # receives the strongest reservation, then same-speaker continuity, request-selected
        # target history, and finally ambient channel chat. Unused reservations flow back.
        remaining = max(0, int(self.budget))
        slots = 18

        active_reply_budget = (
            min(4000, max(1200, remaining * 2 // 3))
            if replied
            else 0
        )
        active_reply_budget = min(remaining, active_reply_budget)
        active_reply_selected, active_reply_unused = self._take_active_reply_bundle(
            active_chain,
            replied,
            active_reply_budget,
            min(6, slots),
        )
        remaining -= active_reply_budget - active_reply_unused
        slots -= len(active_reply_selected)

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

        return (
            target_selected
            + selected_base
            + source_selected
            + active_reply_selected
        )
