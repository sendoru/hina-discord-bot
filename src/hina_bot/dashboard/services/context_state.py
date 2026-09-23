from __future__ import annotations

import re

from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import (
    CHATLOG_CAPTURE_NOTE_PREFIX,
    effective_recent_context_mode,
    memory_mode_capabilities,
    resolve_scope_chain,
)

from .base import ReadService

_GUILD_NOTE = re.compile(r"^guild:(\d+)$")
_GUILD_USER_NOTE = re.compile(r"^guild:(\d+):user:(\d+)$")
_DM_USER_NOTE = re.compile(r"^dm:(\d+):user:(\d+)$")


class ContextStateService(ReadService):
    @staticmethod
    def _mode_map(rows: list[dict[str, object]]) -> dict[str, str]:
        return {
            str(row["scope"]): str(row["mode"])
            for row in rows
            if row.get("scope") and row.get("mode")
        }

    @staticmethod
    def _parse_target(
        guild_id: str,
        channel_id: str,
        user_id: str,
    ) -> tuple[Scope | None, str]:
        guild_id = guild_id.strip()
        channel_id = channel_id.strip()
        user_id = user_id.strip()
        if not (guild_id or channel_id or user_id):
            return None, ""
        if not channel_id or not user_id:
            return None, "Channel ID and user ID are required. Leave guild ID empty for a DM."
        try:
            parsed_channel = int(channel_id)
            parsed_user = int(user_id)
            parsed_guild = int(guild_id) if guild_id else None
            if parsed_channel <= 0 or parsed_user <= 0:
                raise ValueError
            if parsed_guild is not None and parsed_guild <= 0:
                raise ValueError
        except ValueError:
            return None, "Guild/channel/user IDs must be positive integers."
        return Scope(parsed_guild, parsed_channel, parsed_user), ""

    @staticmethod
    def _manual_note_row(
        row: dict[str, object],
        names: dict[tuple[str, str], str],
    ) -> dict[str, object]:
        scope_key = str(row.get("scope") or "")
        text = str(row.get("text") or "")
        kind = "other"
        realm = ""
        user_id = ""
        user_name = ""

        match = _GUILD_NOTE.fullmatch(scope_key)
        if match:
            kind = "server"
            realm = scope_key
        else:
            match = _GUILD_USER_NOTE.fullmatch(scope_key)
            if match:
                kind = "user"
                realm = f"guild:{match.group(1)}"
                user_id = match.group(2)
            else:
                match = _DM_USER_NOTE.fullmatch(scope_key)
                if match:
                    kind = "user"
                    realm = f"dm:{match.group(1)}"
                    user_id = match.group(2)

        if user_id:
            user_name = names.get((realm, user_id), "")

        return {
            "scope": scope_key,
            "text": text,
            "kind": kind,
            "realm": realm,
            "user_id": user_id,
            "user_name": user_name,
        }

    def context_state(
        self,
        *,
        target_guild_id: str = "",
        target_channel_id: str = "",
        target_user_id: str = "",
        query: str = "",
    ) -> dict[str, object]:
        target_guild_id = target_guild_id.strip()
        target_channel_id = target_channel_id.strip()
        target_user_id = target_user_id.strip()
        query = query.strip().lower()

        memory_override_rows = self.repository.scope_mode_overrides("memory_modes")
        chat_override_rows = self.repository.scope_mode_overrides("chat_log_modes")
        note_rows = self.repository.note_rows()
        names = self.repository.latest_user_names()

        memory_overrides = self._mode_map(memory_override_rows)
        chat_overrides = self._mode_map(chat_override_rows)
        capture_overrides: dict[str, str] = {}
        manual_notes = []
        internal_note_count = 0

        for row in note_rows:
            scope_key = str(row.get("scope") or "")
            value = str(row.get("text") or "")
            if scope_key.startswith(CHATLOG_CAPTURE_NOTE_PREFIX):
                target_key = scope_key[len(CHATLOG_CAPTURE_NOTE_PREFIX):]
                if value in {"all", "direct"}:
                    capture_overrides[target_key] = value
                internal_note_count += 1
                continue
            if scope_key.startswith("config:"):
                internal_note_count += 1
                continue
            note = self._manual_note_row(row, names)
            if query:
                haystack = " ".join(
                    (
                        str(note["scope"]),
                        str(note["kind"]),
                        str(note["realm"]),
                        str(note["user_id"]),
                        str(note["user_name"]),
                        str(note["text"]),
                    )
                ).lower()
                if query not in haystack:
                    continue
            manual_notes.append(note)

        scope, target_error = self._parse_target(
            target_guild_id,
            target_channel_id,
            target_user_id,
        )
        effective = None
        if scope is not None:
            memory_chain = resolve_scope_chain(
                memory_overrides,
                scope,
                default="normal",
            )
            chat_chain = resolve_scope_chain(
                chat_overrides,
                scope,
                default="on",
            )
            capture_chain = resolve_scope_chain(
                capture_overrides,
                scope,
                default="all",
            )
            reads, writes = memory_mode_capabilities(str(memory_chain["effective"]))
            recent_mode = effective_recent_context_mode(
                scope,
                chat_log_mode=str(chat_chain["effective"]),
                capture_mode=str(capture_chain["effective"]),
            )
            manual_note_map = {
                str(row.get("scope") or ""): str(row.get("text") or "")
                for row in note_rows
                if not str(row.get("scope") or "").startswith("config:")
            }
            effective = {
                "scope": scope,
                "realm": scope.realm,
                "channel": scope.channel,
                "user_note_key": scope.user_note,
                "memory": {
                    "chain": memory_chain,
                    "reads": reads,
                    "writes": writes,
                },
                "chat_log": {
                    "enabled_chain": chat_chain,
                    "capture_chain": capture_chain,
                    "effective": recent_mode,
                    "guild_recent_context_applicable": scope.guild_id is not None,
                },
                "notes": {
                    "server": (
                        manual_note_map.get(scope.realm, "")
                        if scope.guild_id is not None
                        else ""
                    ),
                    "user": manual_note_map.get(scope.user_note, ""),
                    "eligible_by_memory_mode": reads,
                },
            }

        chat_scope_keys = sorted(set(chat_overrides) | set(capture_overrides))
        chat_override_inventory = [
            {
                "scope": key,
                "enabled": chat_overrides.get(key),
                "capture": capture_overrides.get(key),
            }
            for key in chat_scope_keys
        ]

        return {
            "effective": effective,
            "target_error": target_error,
            "filters": {
                "target_guild_id": target_guild_id,
                "target_channel_id": target_channel_id,
                "target_user_id": target_user_id,
                "q": query,
            },
            "memory_overrides": memory_override_rows,
            "chat_overrides": chat_override_inventory,
            "manual_notes": manual_notes,
            "internal_note_count": internal_note_count,
        }
