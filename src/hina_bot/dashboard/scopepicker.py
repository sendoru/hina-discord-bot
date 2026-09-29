from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeFilter:
    scope_type: str
    guild_id: str
    channel_id: str
    realm: str
    realm_prefix: str


def _clean_scope_type(value: str) -> str:
    value = value.strip().lower()
    return value if value in {"guild", "dm"} else ""


def _realm_parts(realm: str) -> tuple[str, str]:
    realm = realm.strip()
    if realm.startswith("guild:"):
        return "guild", realm.removeprefix("guild:")
    if realm.startswith("dm:"):
        return "dm", realm.removeprefix("dm:")
    return "", ""


def normalize_scope_filter(
    *,
    scope_type: str = "",
    guild_id: str = "",
    channel_id: str = "",
    legacy_realm: str = "",
) -> ScopeFilter:
    """Normalize user-facing Any/Guild/DM filters into repository predicates."""

    scope_type = _clean_scope_type(scope_type)
    guild_id = guild_id.strip()
    channel_id = channel_id.strip()
    legacy_realm = legacy_realm.strip()

    if not scope_type and legacy_realm:
        legacy_type, legacy_id = _realm_parts(legacy_realm)
        if legacy_type == "guild":
            return ScopeFilter(
                scope_type="guild",
                guild_id=legacy_id,
                channel_id=channel_id,
                realm=legacy_realm,
                realm_prefix="",
            )
        if legacy_type == "dm":
            return ScopeFilter(
                scope_type="dm",
                guild_id="",
                channel_id=channel_id or legacy_id,
                realm=legacy_realm,
                realm_prefix="",
            )
        return ScopeFilter(
            scope_type="",
            guild_id="",
            channel_id=channel_id,
            realm=legacy_realm,
            realm_prefix="",
        )

    if scope_type == "guild":
        return ScopeFilter(
            scope_type="guild",
            guild_id=guild_id,
            channel_id=channel_id,
            realm=f"guild:{guild_id}" if guild_id else "",
            realm_prefix="" if guild_id else "guild:",
        )

    if scope_type == "dm":
        return ScopeFilter(
            scope_type="dm",
            guild_id="",
            channel_id=channel_id,
            realm=f"dm:{channel_id}" if channel_id else "",
            realm_prefix="" if channel_id else "dm:",
        )

    return ScopeFilter(
        scope_type="",
        guild_id="",
        channel_id="",
        realm="",
        realm_prefix="",
    )


def infer_target_scope_type(*, scope_type: str = "", guild_id: str = "") -> str:
    """Return picker mode while preserving legacy empty-guild DM URLs."""

    explicit = _clean_scope_type(scope_type)
    if explicit:
        return explicit
    return "guild" if guild_id.strip() else "dm"


__all__ = [
    "ScopeFilter",
    "infer_target_scope_type",
    "normalize_scope_filter",
]
