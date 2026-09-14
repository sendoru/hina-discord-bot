"""Bounded, ephemeral channel context; never an input to long-term summarization."""
import time
from collections import OrderedDict, deque


class RecentMessages:
    def __init__(self, limit=30, ttl=900, channels=128, budget=6000):
        self.limit, self.ttl, self.channels = limit, ttl, channels
        self.budget = budget
        self.buffers = OrderedDict()
        # A channel is marked hydrated after its recent Discord history has been backfilled once.
        # Live Gateway messages do not set this flag, so off->on can still recover messages that
        # arrived while reading was disabled.
        self.hydrated = set()

    @staticmethod
    def _key(scope):
        return (scope.realm, scope.channel_id)

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
        now = time.monotonic()
        wall_now = time.time()
        timestamp = wall_now if unix_time is None else min(float(unix_time), wall_now)
        age = max(0.0, wall_now - timestamp)
        received_at = now - age
        if age >= self.ttl:
            return

        # `user_id` is retained for existing consumers, but it now always means the author.
        # Assistant reply targets are separate metadata and must never be encoded as the author.
        if author_user_id is None and role != "assistant":
            author_user_id = scope.user_id
        author_id = None if author_user_id is None else str(author_user_id)
        reply_target_id = (
            None if reply_target_user_id is None else str(reply_target_user_id)
        )

        key = self._key(scope)
        self.prune(now)
        existing = list(self.buffers.get(key, ()))
        if any(row["message_id"] == message_id for row in existing):
            return
        existing.append({
            "message_id": message_id,
            "user_id": author_id or "",
            "author_user_id": author_id,
            "reply_target_user_id": reply_target_id,
            "name": name[:100],
            "content": content[:4000],
            "role": role,
            "received_at": received_at,
            "unix_time": timestamp,
        })
        # Historical backfill may arrive after a live trigger message. Discord snowflake IDs are
        # chronological, so sort the tiny bounded buffer to keep conversation order correct.
        existing.sort(key=lambda row: row["message_id"])
        self.buffers[key] = deque(existing[-self.limit:], maxlen=self.limit)
        self.buffers.move_to_end(key)
        while len(self.buffers) > self.channels:
            old_key, _ = self.buffers.popitem(last=False)
            self.hydrated.discard(old_key)

    def prune(self, now):
        for key, rows in list(self.buffers.items()):
            while rows and now - rows[0]["received_at"] >= self.ttl:
                rows.popleft()
            if not rows:
                del self.buffers[key]
                self.hydrated.discard(key)

    def needs_hydration(self, scope):
        self.prune(time.monotonic())
        return self._key(scope) not in self.hydrated

    def mark_hydrated(self, scope):
        self.hydrated.add(self._key(scope))

    def context(self, scope, before_id):
        self.prune(time.monotonic())
        result, remaining = [], self.budget
        for row in reversed(self.buffers.get(self._key(scope), ())):
            if row["message_id"] >= before_id:
                continue
            if remaining <= 0 or len(result) >= 12:
                break
            item = dict(row)
            item["content"] = item["content"][:remaining]
            remaining -= len(item["content"])
            result.append(item)
        return list(reversed(result))

    def forget(self, scope):
        # Bot replies can paraphrase deleted material: invalidate all recent context in this realm.
        for key in list(self.buffers):
            if key[0] == scope.realm:
                del self.buffers[key]
                self.hydrated.discard(key)
        for key in list(self.hydrated):
            if key[0] == scope.realm:
                self.hydrated.discard(key)

    def clear_channel(self, scope):
        key = self._key(scope)
        self.buffers.pop(key, None)
        self.hydrated.discard(key)

    def clear_all(self):
        self.buffers.clear()
        self.hydrated.clear()
