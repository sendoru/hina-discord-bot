"""Parse stored scope keys for display, without changing runtime scope policy."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCOPE_KEY = re.compile(
    r"(?P<realm_kind>guild|dm):(?P<realm_id>\d+)"
    r"(?::channel:(?P<channel_id>\d+))?"
    r"(?::user:(?P<user_id>\d+))?"
)


@dataclass(frozen=True)
class ScopePresentation:
    raw: str
    level: str
    realm_kind: str = ""
    realm_id: str = ""
    channel_id: str = ""
    user_id: str = ""

    @property
    def realm(self) -> str:
        return f"{self.realm_kind}:{self.realm_id}" if self.realm_kind else ""


def parse_scope_key(raw: str) -> ScopePresentation:
    """Preserve IDs verbatim and keep unsupported keys visible as unknown.

    A DM realm ID identifies its owner; it is separate from an explicit user
    component. Recognizing a key does not make it an eligible override or note.
    """

    if raw == "global":
        return ScopePresentation(raw=raw, level="global")
    match = _SCOPE_KEY.fullmatch(raw)
    if match is None:
        return ScopePresentation(raw=raw, level="unknown")
    channel_id = match.group("channel_id") or ""
    user_id = match.group("user_id") or ""
    if channel_id and user_id:
        level = "conversation"
    elif channel_id:
        level = "channel"
    elif user_id:
        level = "user"
    else:
        level = "realm"
    return ScopePresentation(
        raw=raw,
        level=level,
        realm_kind=match.group("realm_kind"),
        realm_id=match.group("realm_id"),
        channel_id=channel_id,
        user_id=user_id,
    )
