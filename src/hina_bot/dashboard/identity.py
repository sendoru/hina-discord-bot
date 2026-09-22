from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from math import ceil

from .telemetry import TelemetrySnapshot
from .timeutils import parse_local_time, quick_ranges


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _in_window(
    row: dict[str, object],
    after: str,
    before: str,
    timezone: str,
) -> bool:
    row_dt = _parse_time(row.get("at"))
    after_dt = parse_local_time(after, timezone)
    before_dt = parse_local_time(before, timezone)
    if after_dt and (row_dt is None or row_dt < after_dt):
        return False
    return not (before_dt and (row_dt is None or row_dt > before_dt))


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _usage_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    tokens = [
        value
        for row in rows
        if isinstance((value := row.get("total_tokens")), int)
        and not isinstance(value, bool)
    ]
    latencies = [
        value
        for row in rows
        if isinstance((value := row.get("elapsed_ms")), int)
        and not isinstance(value, bool)
    ]
    return {
        "calls": len(rows),
        "errors": sum(row.get("status") == "error" for row in rows),
        "total_tokens": sum(tokens),
        "token_known": len(tokens),
        "token_missing": len(rows) - len(tokens),
        "latency_known": len(latencies),
        "latency_missing": len(rows) - len(latencies),
        "latency_average": round(sum(latencies) / len(latencies)) if latencies else None,
        "latency_p50": _percentile(latencies, 0.50),
        "latency_p95": _percentile(latencies, 0.95),
        "models": dict(Counter(
            str(row["model"])
            for row in rows
            if isinstance(row.get("model"), str) and row.get("model")
        )),
        "providers": dict(Counter(
            str(row["provider"])
            for row in rows
            if isinstance(row.get("provider"), str) and row.get("provider")
        )),
    }


def _candidate_buckets(events: list[dict[str, object]]) -> list[dict[str, object]]:
    buckets = [
        ("1–4", 1, 4),
        ("5–8", 5, 8),
        ("9–16", 9, 16),
        ("17–32", 17, 32),
    ]
    return [
        {
            "label": label,
            "count": sum(
                1
                for row in events
                if isinstance(row.get("candidate_count"), int)
                and low <= int(row["candidate_count"]) <= high
            ),
        }
        for label, low, high in buckets
    ]


def _repeat_groups(events: list[dict[str, object]]) -> tuple[list[dict[str, object]], int, int]:
    grouped: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "events": 0,
            "resolved": 0,
            "ambiguous": 0,
            "none": 0,
            "potential_hits": 0,
            "conflicts": 0,
            "users": set(),
        }
    )
    cache: dict[str, str] = {}
    total_hits = 0
    conflicts = 0

    for row in sorted(events, key=lambda item: str(item.get("at") or "")):
        reference = row.get("reference_group")
        if not isinstance(reference, str) or not reference:
            continue
        state = grouped[reference]
        state["events"] = int(state["events"]) + 1
        outcome = str(row.get("outcome") or "")
        if outcome == "resolved":
            state["resolved"] = int(state["resolved"]) + 1
            user = row.get("resolved_user_group")
            if isinstance(user, str) and user:
                users = state["users"]
                assert isinstance(users, set)
                users.add(user)
                previous = cache.get(reference)
                if previous == user:
                    state["potential_hits"] = int(state["potential_hits"]) + 1
                    total_hits += 1
                elif previous and previous != user:
                    state["conflicts"] = int(state["conflicts"]) + 1
                    conflicts += 1
                cache[reference] = user
        elif outcome == "ambiguous":
            state["ambiguous"] = int(state["ambiguous"]) + 1
            cache.pop(reference, None)
        elif outcome == "none":
            state["none"] = int(state["none"]) + 1
            cache.pop(reference, None)

    rows = []
    for reference, value in grouped.items():
        if int(value["events"]) < 2:
            continue
        users = value["users"]
        assert isinstance(users, set)
        rows.append(
            {
                "reference_group": reference,
                "events": int(value["events"]),
                "resolved": int(value["resolved"]),
                "ambiguous": int(value["ambiguous"]),
                "none": int(value["none"]),
                "potential_hits": int(value["potential_hits"]),
                "conflicts": int(value["conflicts"]),
                "distinct_users": len(users),
                "stable": len(users) == 1 and int(value["conflicts"]) == 0,
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row["potential_hits"]),
            -int(row["events"]),
            str(row["reference_group"]),
        )
    )
    return rows, total_hits, conflicts


def build_identity_observability(
    snapshot: TelemetrySnapshot,
    *,
    outcome: str = "",
    blocked_reason: str = "",
    after: str = "",
    before: str = "",
    timezone: str = "Asia/Seoul",
) -> dict[str, object]:
    outcome = outcome.strip().lower()
    blocked_reason = blocked_reason.strip().lower()
    after = after.strip()
    before = before.strip()

    all_events = [
        row
        for row in snapshot.events
        if row.get("event") == "identity.resolution"
        and _in_window(row, after, before, timezone)
    ]
    invoked = [row for row in all_events if row.get("resolver_invoked") is True]
    usage = [
        row
        for row in snapshot.usage
        if row.get("operation") == "identity_resolve"
        and _in_window(row, after, before, timezone)
    ]

    outcomes = Counter(str(row.get("outcome") or "unknown") for row in all_events)
    blocked = Counter(
        str(row["blocked_reason"])
        for row in all_events
        if isinstance(row.get("blocked_reason"), str) and row.get("blocked_reason")
    )
    repeat_rows, potential_hits, conflicts = _repeat_groups(invoked)

    visible = []
    for row in sorted(all_events, key=lambda item: str(item.get("at") or ""), reverse=True):
        row_outcome = str(row.get("outcome") or "")
        row_blocked = str(row.get("blocked_reason") or "")
        if outcome and row_outcome != outcome:
            continue
        if blocked_reason and row_blocked != blocked_reason:
            continue
        visible.append(row)
        if len(visible) >= 100:
            break

    stable_repeat_groups = sum(bool(row["stable"]) for row in repeat_rows)
    conflicting_groups = sum(int(row["conflicts"]) > 0 for row in repeat_rows)
    invoked_count = len(invoked)

    return {
        "available": {
            "oldest_at": snapshot.oldest_at,
            "newest_at": snapshot.newest_at,
        },
        "filters": {
            "outcome": outcome,
            "blocked_reason": blocked_reason,
            "after": after,
            "before": before,
        },
        "time_ranges": quick_ranges(timezone),
        "summary": {
            "events": len(all_events),
            "invoked": invoked_count,
            "resolved": outcomes.get("resolved", 0),
            "ambiguous": outcomes.get("ambiguous", 0),
            "none": outcomes.get("none", 0),
            "blocked": outcomes.get("blocked", 0),
            "potential_cache_hits": potential_hits,
            "potential_hit_rate": (
                round(potential_hits * 100 / invoked_count, 1) if invoked_count else 0.0
            ),
            "repeat_groups": len(repeat_rows),
            "stable_repeat_groups": stable_repeat_groups,
            "conflicting_groups": conflicting_groups,
            "mapping_conflicts": conflicts,
            "assistant_generated_evidence": sum(
                row.get("evidence_source") == "assistant_generated"
                for row in all_events
            ),
        },
        "outcomes": dict(outcomes),
        "blocked_reasons": dict(blocked),
        "candidate_buckets": _candidate_buckets(invoked),
        "usage": _usage_summary(usage),
        "repeat_groups": repeat_rows[:100],
        "recent": visible,
    }


__all__ = ["build_identity_observability"]
