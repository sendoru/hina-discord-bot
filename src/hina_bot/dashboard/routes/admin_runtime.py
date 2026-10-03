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


def _runtime_rows(repository: AdminRepository) -> list[dict[str, object]]:
    overrides = {
        str(row["key"]): row
        for row in repository.runtime_config_rows()
    }
    rows: list[dict[str, object]] = []
    for key, spec in RUNTIME_SETTING_SPECS.items():
        stored = overrides.get(key)
        value = None
        error = ""
        if stored is not None:
            try:
                value = format_runtime_value(
                    decode_runtime_value(spec, str(stored["value"]))
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                error = type(exc).__name__
        rows.append(
            {
                "key": key,
                "env_name": spec.env_name,
                "kind": spec.kind,
                "source": "db" if stored is not None else "startup",
                "value": value,
                "updated_at": stored.get("updated_at") if stored else None,
                "error": error,
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
