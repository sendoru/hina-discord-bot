from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from hina_bot.core.runtime_config import (
    RUNTIME_SETTING_SPECS,
    decode_runtime_value,
    format_runtime_value,
)

from ..admin_write import AdminCommandWriter
from ..repository import AdminRepository


def _runtime_edit_value(value: object) -> str:
    if isinstance(value, tuple):
        return ", ".join(value)
    if isinstance(value, (set, frozenset)):
        return ", ".join(str(item) for item in sorted(value))
    if isinstance(value, bool):
        return "on" if value else "off"
    if value == "":
        return ""
    return str(value)


def _runtime_rows(repository: AdminRepository) -> list[dict[str, object]]:
    overrides = {
        str(row["key"]): row
        for row in repository.runtime_config_rows()
    }
    startup = {
        str(row["key"]): row
        for row in repository.runtime_config_startup_rows()
    }
    rows: list[dict[str, object]] = []
    for key, spec in RUNTIME_SETTING_SPECS.items():
        stored = overrides.get(key)
        snapshot = startup.get(key)

        startup_value = None
        startup_edit_value = ""
        startup_error = ""
        if snapshot is not None:
            try:
                decoded_startup = decode_runtime_value(spec, str(snapshot["value"]))
                startup_value = format_runtime_value(decoded_startup)
                startup_edit_value = _runtime_edit_value(decoded_startup)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                startup_error = type(exc).__name__

        override_value = None
        override_edit_value = ""
        override_error = ""
        if stored is not None:
            try:
                decoded_override = decode_runtime_value(spec, str(stored["value"]))
                override_value = format_runtime_value(decoded_override)
                override_edit_value = _runtime_edit_value(decoded_override)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                override_error = type(exc).__name__

        override_valid = stored is not None and not override_error
        effective_value = override_value if override_valid else startup_value
        effective_edit_value = (
            override_edit_value if override_valid else startup_edit_value
        )
        rows.append(
            {
                "key": key,
                "env_name": spec.env_name,
                "kind": spec.kind,
                "source": "db" if override_valid else "startup",
                "value": effective_value,
                "edit_value": effective_edit_value,
                "startup_value": startup_value,
                "startup_captured_at": (
                    snapshot.get("captured_at") if snapshot else None
                ),
                "startup_error": startup_error,
                "has_override": stored is not None,
                "override_value": override_value,
                "override_error": override_error,
                "updated_at": stored.get("updated_at") if stored else None,
            }
        )
    return rows


def build_router(
    repository: AdminRepository,
    writer: AdminCommandWriter,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/runtime", response_class=HTMLResponse)
    def runtime_config(request: Request, queued: int | None = None):
        return templates.TemplateResponse(
            request=request,
            name="admin_runtime.html",
            context={
                "rows": _runtime_rows(repository),
                "actions": repository.admin_command_rows(
                    action_prefix="runtime.",
                    limit=30,
                ),
                "queued": queued,
            },
        )

    @router.post("/admin/runtime/set")
    async def set_runtime_config(request: Request):
        form = await writer.form(request)
        key = form.get("key", "")
        if key not in RUNTIME_SETTING_SPECS:
            raise HTTPException(status_code=422, detail="unknown runtime setting")
        value = form.get("value", "")
        request_id = form.get("_request_id", "")
        command_id, _ = writer.enqueue(
            request_id=request_id,
            action="runtime.set",
            target=key,
            payload={"key": key, "value": value},
        )
        return RedirectResponse(
            url=f"/admin/runtime?queued={command_id}",
            status_code=303,
        )

    @router.post("/admin/runtime/reset")
    async def reset_runtime_config(request: Request):
        form = await writer.form(request)
        key = form.get("key", "")
        if key not in RUNTIME_SETTING_SPECS:
            raise HTTPException(status_code=422, detail="unknown runtime setting")
        request_id = form.get("_request_id", "")
        command_id, _ = writer.enqueue(
            request_id=request_id,
            action="runtime.reset",
            target=key,
            payload={"key": key},
        )
        return RedirectResponse(
            url=f"/admin/runtime?queued={command_id}",
            status_code=303,
        )

    return router
