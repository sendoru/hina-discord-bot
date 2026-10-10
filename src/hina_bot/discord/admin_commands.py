from __future__ import annotations

import asyncio
import logging

from hina_bot.core.admin_commands import (
    AdminCommand,
    claim_next_admin_command,
    finish_admin_command,
    mark_interrupted_admin_commands,
)
from hina_bot.core.routing import Scope
from hina_bot.core.chatlog_modes import set_unified_chatlog_mode
from hina_bot.core.runtime_config import RuntimeSettings, format_runtime_value
from hina_bot.core.scope_overrides import scope_target_key

from .config_commands import apply_runtime_setting_side_effects

log = logging.getLogger("hina")


def _memory_item_scope(item) -> Scope:
    try:
        user_id = int(item.user_id)
        channel_id = int(item.origin_channel_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("stored memory item has invalid scope") from exc
    if user_id <= 0 or channel_id <= 0:
        raise ValueError("stored memory item has invalid scope")

    realm = str(item.origin_realm)
    if realm == f"dm:{user_id}":
        guild_id = None
    elif realm.startswith("guild:"):
        try:
            guild_id = int(realm.removeprefix("guild:"))
        except ValueError as exc:
            raise ValueError("stored memory item has invalid scope") from exc
        if guild_id <= 0:
            raise ValueError("stored memory item has invalid scope")
    else:
        raise ValueError("stored memory item has invalid scope")
    return Scope(
        guild_id,
        channel_id,
        user_id,
        public_at_capture=bool(item.origin_public_at_capture),
    )


async def execute_admin_command(client, command: AdminCommand) -> dict[str, object]:
    """Dispatch one typed dashboard command.

    Domain handlers are added by dashboard editing feature branches. Keeping the
    executor in the Discord runtime means side effects such as recent-buffer clears
    happen in the owning bot process rather than in the dashboard process.
    """

    if command.action in {"runtime.set", "runtime.reset"}:
        if not isinstance(client.settings, RuntimeSettings):
            raise ValueError("runtime settings are not hot-reloadable in this process")
        key = str(command.payload.get("key") or command.target).strip()
        if command.action == "runtime.set":
            value = str(command.payload.get("value") or "")
            parsed = client.settings.set_text(key, value)
        else:
            parsed = client.settings.reset(key)
        apply_runtime_setting_side_effects(client, key)
        return {
            "key": key,
            "value": format_runtime_value(parsed),
            "source": client.settings.source(key),
        }

    if command.action == "memory.item.edit":
        payload = command.payload
        try:
            item_id = int(payload["item_id"])
            expected_revision = int(payload["expected_revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid memory item edit target") from exc
        if item_id <= 0 or expected_revision < 0:
            raise ValueError("invalid memory item edit target")

        item = client.store.memory_item(item_id)
        if item is None:
            raise ValueError("Memory item not found")
        scope = _memory_item_scope(item)
        async with client.memory_lock(scope):
            revised = client.store.revise_memory_item(
                item_id,
                expected_revision=expected_revision,
                content=payload.get("content"),
                kind=payload.get("kind"),
                disclosure=payload.get("disclosure"),
                confidence=payload.get("confidence"),
                relationship_evidence=payload.get("relationship_evidence", {}),
                admin_command_id=command.id,
            )
        return {
            "item_id": item_id,
            "revision": revised.revision,
            "changed": revised.revision != expected_revision,
        }

    if command.action == "memory.item.retract":
        payload = command.payload
        try:
            item_id = int(payload["item_id"])
            expected_revision = int(payload["expected_revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid memory item retract target") from exc
        if item_id <= 0 or expected_revision < 0:
            raise ValueError("invalid memory item retract target")

        item = client.store.memory_item(item_id)
        if item is None:
            raise ValueError("Memory item not found")
        scope = _memory_item_scope(item)
        async with client.memory_lock(scope):
            retracted = client.store.retract_memory_item(
                item_id,
                expected_revision=expected_revision,
            )
        return {
            "item_id": item_id,
            "revision": retracted.revision,
            "status": retracted.status.value,
        }

    if command.action.startswith(("memory.", "chatlog.", "note.")):
        payload = command.payload
        guild_raw = payload.get("guild_id")
        channel_raw = payload.get("channel_id")
        user_raw = payload.get("user_id")
        try:
            guild_id = None if guild_raw in (None, "") else int(guild_raw)
            channel_id = int(channel_raw)
            user_id = int(user_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid admin scope") from exc
        if channel_id <= 0 or user_id <= 0 or (guild_id is not None and guild_id <= 0):
            raise ValueError("invalid admin scope")
        scope = Scope(guild_id, channel_id, user_id)

        if command.action == "memory.mode":
            target = str(payload.get("target") or "channel")
            mode = str(payload.get("mode") or "")
            if mode not in {"normal", "read_only", "write_only", "off", "inherit"}:
                raise ValueError("invalid memory mode")
            key = scope_target_key(scope, target)
            if target == "global" and mode == "inherit":
                raise ValueError("global memory mode cannot inherit")
            async with client.channel_lock(scope):
                client.store.set_memory_mode_override(
                    key,
                    None if mode == "inherit" else mode,
                )
            return {"target": target, "scope": key, "mode": mode}

        if command.action == "memory.purge":
            target = str(payload.get("target") or "channel")
            async with client.channel_lock(scope):
                if target == "channel":
                    deleted = client.store.purge_channel_memory(scope)
                elif target == "server":
                    if scope.guild_id is None:
                        raise ValueError("DM scope cannot purge a server")
                    deleted = client.store.purge_realm_memory(scope)
                elif target == "global":
                    deleted = client.store.purge_all_memory()
                else:
                    raise ValueError("invalid memory purge target")
            return {"target": target, "deleted": deleted}

        if command.action == "chatlog.mode":
            target = str(payload.get("target") or "channel")
            mode = str(payload.get("mode") or "")
            if mode not in {"all", "direct", "off", "inherit"}:
                raise ValueError("invalid chatlog mode")
            key = {
                "global": "global",
                "server": scope.realm,
                "channel": scope.channel,
            }.get(target)
            if key is None or (target == "server" and scope.guild_id is None):
                raise ValueError("invalid chatlog target")
            if target == "global" and mode == "inherit":
                raise ValueError("global chatlog mode cannot inherit")
            async with client.channel_lock(scope):
                set_unified_chatlog_mode(
                    client.store,
                    key,
                    None if mode == "inherit" else mode,
                )
                if target == "global":
                    client.recent.clear_all()
                elif target == "server":
                    client.recent.forget(scope)
                else:
                    client.recent.clear_channel(scope)
            return {"target": target, "scope": key, "mode": mode}

        if command.action == "chatlog.clear":
            async with client.channel_lock(scope):
                client.recent.clear_channel(scope)
            return {"scope": scope.channel, "cleared": True}

        if command.action in {"note.set", "note.clear"}:
            note_target = str(payload.get("target") or "user")
            if note_target == "user":
                key = scope.user_note
            elif note_target == "server" and scope.guild_id is not None:
                key = scope.realm
            else:
                raise ValueError("invalid note target")
            text = "" if command.action == "note.clear" else str(payload.get("text") or "").strip()
            if command.action == "note.set" and not 1 <= len(text) <= 1500:
                raise ValueError("note must be 1..1500 characters")
            async with client.channel_lock(scope):
                if note_target == "user":
                    async with client.memory_lock(scope):
                        client.store.set_note(key, text)
                else:
                    client.store.set_note(key, text)
            return {"target": note_target, "scope": key, "cleared": not bool(text)}

    if command.action.startswith("instruction."):
        registry = client.llm.instructions
        identifier = str(command.payload.get("id") or command.target).strip()
        if command.action == "instruction.add":
            registry.add(identifier, str(command.payload.get("text") or ""))
            return {"id": identifier, "action": "added"}
        if command.action == "instruction.edit":
            registry.edit(identifier, str(command.payload.get("text") or ""))
            return {"id": identifier, "action": "edited"}
        if command.action == "instruction.state":
            enabled = command.payload.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("instruction enabled must be boolean")
            registry.set_enabled(identifier, enabled)
            return {"id": identifier, "enabled": enabled}
        if command.action == "instruction.remove":
            registry.remove(identifier)
            return {"id": identifier, "action": "removed"}

    if command.action.startswith("knowledge."):
        kind = str(command.payload.get("kind") or "")
        if kind == "world_fact":
            registry = client.llm.runtime_lore
        elif kind == "interpretation":
            registry = client.llm.story_context
        else:
            raise ValueError("invalid runtime knowledge kind")
        identifier = str(command.payload.get("id") or command.target).strip()

        if command.action == "knowledge.add":
            registry.add(
                identifier,
                str(command.payload.get("content") or ""),
                str(command.payload.get("keywords") or ""),
                str(command.payload.get("subjects") or ""),
                str(command.payload.get("awareness") or ""),
                str(command.payload.get("timeline") or ""),
            )
            return {"id": identifier, "kind": kind, "action": "added"}
        if command.action == "knowledge.edit":
            registry.edit(
                identifier,
                content=str(command.payload.get("content") or ""),
                keywords=str(command.payload.get("keywords") or ""),
                subjects=str(command.payload.get("subjects") or ""),
                awareness=str(command.payload.get("awareness") or ""),
                timeline=str(command.payload.get("timeline") or ""),
            )
            return {"id": identifier, "kind": kind, "action": "edited"}
        if command.action == "knowledge.state":
            enabled = command.payload.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("knowledge enabled must be boolean")
            registry.set_enabled(identifier, enabled)
            return {"id": identifier, "kind": kind, "enabled": enabled}
        if command.action == "knowledge.remove":
            registry.remove(identifier)
            return {"id": identifier, "kind": kind, "action": "removed"}

    raise ValueError(f"unsupported admin action: {command.action}")


def initialize_admin_commands(client) -> None:
    interrupted = mark_interrupted_admin_commands(client.store.db)
    if interrupted:
        log.warning("Marked %s interrupted dashboard admin command(s) failed", interrupted)


async def run_admin_command_once(client) -> bool:
    command = claim_next_admin_command(client.store.db)
    if command is None:
        return False

    try:
        result = await execute_admin_command(client, command)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - isolate operator action failures
        finish_admin_command(
            client.store.db,
            command.id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        client.events.emit(
            "admin.command_failed",
            level="warning",
            action=command.action,
            target=command.target,
            command_id=command.id,
            error_type=type(exc).__name__,
        )
        log.warning(
            "Dashboard admin command failed (%s, action=%s, id=%s)",
            type(exc).__name__,
            command.action,
            command.id,
        )
    else:
        finish_admin_command(client.store.db, command.id, result=result)
        client.events.emit(
            "admin.command_completed",
            action=command.action,
            target=command.target,
            command_id=command.id,
        )
    return True
