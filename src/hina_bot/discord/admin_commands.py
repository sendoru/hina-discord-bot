from __future__ import annotations

import asyncio
import logging

from hina_bot.core.runtime_config import RuntimeSettings, format_runtime_value

from .config_commands import apply_runtime_setting_side_effects

from hina_bot.core.admin_commands import (
    AdminCommand,
    claim_next_admin_command,
    finish_admin_command,
    mark_interrupted_admin_commands,
)

log = logging.getLogger("hina")


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
