"""Deterministic session boundary for automatically injected user–assistant turns.

This filters *reads* only. Stored history, long-term memory, explicit reply chains, and
on-demand history retrieval have separate retention/access policies.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

# An active chat can continue across many short pauses, but its automatic context
# should not carry an indefinitely old conversation along with it.
MAX_AUTOMATIC_SESSION_AGE = timedelta(hours=24)


def _turn_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(
                raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
            )
        except ValueError:
            return None
    else:
        return None

    # SQLite CURRENT_TIMESTAMP is UTC without an explicit offset.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def active_session_turns(
    turns: Iterable,
    *,
    gap_minutes: int = 240,
    now: datetime | None = None,
) -> list:
    """Return the newest contiguous completed-turn session, oldest first.

    `turns` is already a bounded, chronologically ordered `store.history(scope)`
    result. Each step compares the timestamp to the next newer turn (initially
    the *current request time*). At the first invalid timestamp, gap greater
    than the configured limit, or age greater than 24h, stop rather than
    backfilling from an unrelated past session.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    gap = timedelta(minutes=gap_minutes)
    earliest = current - MAX_AUTOMATIC_SESSION_AGE
    next_time = current
    selected = []
    for turn in reversed(list(turns)):
        timestamp = _turn_time(turn["created_at"])
        if (
            timestamp is None
            or timestamp > next_time
            or timestamp < earliest
            or next_time - timestamp > gap
        ):
            break
        selected.append(turn)
        next_time = timestamp
    return list(reversed(selected))
