"""Bounded, one-turn identity evidence, independent of memory and history."""

from contextvars import ContextVar

CURRENT_RESOLVED_IDENTITIES: ContextVar[tuple[dict, ...]] = ContextVar(
    "current_resolved_identities", default=(),
)


def resolved_identity_context() -> list[dict]:
    """Serialize only the selected identities, never a candidate directory."""
    rows, seen = [], set()
    for row in CURRENT_RESOLVED_IDENTITIES.get():
        user_id = str(row.get("user_id") or "")
        if not user_id.isdecimal() or int(user_id) <= 0 or user_id in seen:
            continue
        seen.add(user_id)
        names = tuple(dict.fromkeys(
            str(value)[:100] for value in row.get("names", ()) if str(value).strip()
        ))[:4]
        rows.append({
            "user_id": user_id,
            "reference": str(row.get("reference") or "")[:120],
            "names": list(names),
        })
        if len(rows) == 2:
            break
    return rows


__all__ = ["CURRENT_RESOLVED_IDENTITIES", "resolved_identity_context"]
