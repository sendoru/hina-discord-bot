from .recent import RecentMessages
from .target_context import TARGET_CONTEXT


class TargetAwareRecentMessages(RecentMessages):
    def context(self, scope, before_id):
        base = super().context(scope, before_id)
        seen = {str(row.get("message_id", "")) for row in base}
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
        return extra + base
