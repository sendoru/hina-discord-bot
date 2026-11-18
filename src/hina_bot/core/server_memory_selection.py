"""Bounded selection over authorized community memory rows."""
import json
import re
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from .memory_selection import is_light_chat, similar_memory_text

CURRENT_SERVER_READER = ContextVar("server_memory_reader", default=None)


def select_server_rows(rows, query, *, lookup=False, now=None):
    now = now or datetime.now(UTC)
    terms = set(re.findall(r"[\w가-힣]{2,}", query.casefold()))
    casual = is_light_chat(query)
    scored = []
    for row in rows:
        text = row["content"]
        words = set(re.findall(r"\w{2,}", text.casefold()))
        hits = len(terms & words) + sum(
            2 for keyword in json.loads(row["keywords"]) if keyword.casefold() in query.casefold()
        )
        if row["usage_mode"] == "directive" and row["trust"] == "admin" and not lookup:
            tier = "S0"
        elif hits:
            tier = "S3" if lookup else "S2"
        elif not lookup and casual and row["usage_mode"] == "ambient" and row["trust"] != "single":
            if row["time_scope"] == "planned":
                try:
                    days = (datetime.fromisoformat(row["event_at"]).date() - now.date()).days
                except (TypeError, ValueError):
                    continue
                if not 0 <= days <= 7:
                    continue
            elif row["time_scope"] not in {"ongoing", "recurring"}:
                continue
            if row["last_used_at"]:
                used = datetime.fromisoformat(row["last_used_at"])
                if now - (used if used.tzinfo else used.replace(tzinfo=UTC)) < timedelta(hours=72):
                    continue
            tier = "S1"
        else:
            continue
        scored.append((tier, -hits, -row["importance"], -row["id"], row))
    result = []
    counts, sizes, seen = {}, {}, set()
    for tier, _, _, _, row in sorted(scored, key=lambda entry: entry[:4]):
        text = row["content"]
        if any(text == other or similar_memory_text(text, other) for other in seen) or counts.get(tier, 0) >= {"S0": 3, "S1": 1, "S2": 3, "S3": 3}[tier]:
            continue
        if sizes.get(tier, 0) + len(text) > 800:
            continue
        result.append({"id": row["id"], "store": row.get("store", "server"), "tier": tier, "text": text, "trust": row["trust"],
                       "time_scope": row["time_scope"], "date": row["event_at"] or row["observed_at"],
                       "subject_user_ids": json.loads(row["subject_user_ids"])})
        counts[tier] = counts.get(tier, 0) + 1
        sizes[tier] = sizes.get(tier, 0) + len(text)
        seen.add(text)
    return result
