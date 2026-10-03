from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from hina_bot.core.routing import Scope

from ..admin_write import AdminCommandWriter
from ..services import ContextStateService


def _positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{label} must be a positive integer") from exc
    if parsed <= 0:
        raise HTTPException(status_code=422, detail=f"{label} must be a positive integer")
    return parsed


def _scope_from_form(form: dict[str, str]) -> Scope:
    scope_type = form.get("target_scope_type", "")
    user_id = _positive_int(form.get("target_user_id", ""), "user id")
    if scope_type == "guild":
        guild_id = _positive_int(form.get("target_guild_id", ""), "guild id")
        channel_id = _positive_int(form.get("target_channel_id", ""), "channel id")
        return Scope(guild_id, channel_id, user_id)
    if scope_type == "dm":
        channel_id = _positive_int(form.get("target_dm_channel_id", ""), "DM channel id")
        return Scope(None, channel_id, user_id)
    raise HTTPException(status_code=422, detail="select a concrete guild or DM scope first")


def _scope_payload(scope: Scope) -> dict[str, object]:
    return {
        "guild_id": scope.guild_id,
        "channel_id": scope.channel_id,
        "user_id": scope.user_id,
    }


def _return_url(form: dict[str, str], command_id: int) -> str:
    keys = (
        "target_scope_type",
        "target_guild_id",
        "target_channel_id",
        "target_dm_channel_id",
        "target_user_id",
        "q",
    )
    query = {key: form.get(key, "") for key in keys if form.get(key, "")}
    query["queued"] = str(command_id)
    return "/state?" + urlencode(query)


def build_router(
    service: ContextStateService,
    writer: AdminCommandWriter,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    @router.get("/state", response_class=HTMLResponse)
    def context_state(
        request: Request,
        target_scope_type: str = "",
        target_guild_id: str = "",
        target_channel_id: str = "",
        target_dm_channel_id: str = "",
        target_user_id: str = "",
        q: str = "",
        queued: int | None = None,
    ):
        data = service.context_state(
            target_scope_type=target_scope_type,
            target_guild_id=target_guild_id,
            target_channel_id=target_channel_id,
            target_dm_channel_id=target_dm_channel_id,
            target_user_id=target_user_id,
            query=q,
        )
        data["admin_actions"] = [
            row
            for row in service.repository.admin_command_rows(limit=50)
            if str(row.get("action") or "").startswith(("memory.", "chatlog.", "note."))
        ][:30]
        data["queued"] = queued
        return templates.TemplateResponse(
            request=request,
            name="state.html",
            context={"data": data},
        )

    @router.post("/state/admin")
    async def state_admin(request: Request):
        form = await writer.form(request)
        scope = _scope_from_form(form)
        action = form.get("action", "")
        request_id = form.get("_request_id", "")
        payload = _scope_payload(scope)

        if action == "memory.mode":
            target = form.get("target", "channel")
            mode = form.get("mode", "")
            if target not in {"channel", "server", "global"}:
                raise HTTPException(status_code=422, detail="invalid memory target")
            if target == "server" and scope.guild_id is None:
                raise HTTPException(status_code=422, detail="DM scope has no server target")
            if mode not in {"normal", "read_only", "write_only", "off", "inherit"}:
                raise HTTPException(status_code=422, detail="invalid memory mode")
            if target == "global" and mode == "inherit":
                raise HTTPException(status_code=422, detail="global memory mode cannot inherit")
            payload.update({"target": target, "mode": mode})
            queue_target = {"channel": scope.channel, "server": scope.realm, "global": "global"}[target]

        elif action == "memory.purge":
            target = form.get("target", "channel")
            if form.get("confirm") != "yes":
                raise HTTPException(status_code=422, detail="purge confirmation is required")
            if target not in {"channel", "server", "global"}:
                raise HTTPException(status_code=422, detail="invalid purge target")
            if target == "server" and scope.guild_id is None:
                raise HTTPException(status_code=422, detail="DM scope has no server target")
            payload["target"] = target
            queue_target = {"channel": scope.channel, "server": scope.realm, "global": "global"}[target]

        elif action == "chatlog.mode":
            target = form.get("target", "channel")
            mode = form.get("mode", "")
            if target not in {"channel", "server", "global"}:
                raise HTTPException(status_code=422, detail="invalid chatlog target")
            if target == "server" and scope.guild_id is None:
                raise HTTPException(status_code=422, detail="DM scope has no server target")
            if mode not in {"all", "direct", "off", "inherit"}:
                raise HTTPException(status_code=422, detail="invalid chatlog mode")
            if target == "global" and mode == "inherit":
                raise HTTPException(status_code=422, detail="global chatlog mode cannot inherit")
            payload.update({"target": target, "mode": mode})
            queue_target = {"channel": scope.channel, "server": scope.realm, "global": "global"}[target]

        elif action == "chatlog.clear":
            queue_target = scope.channel

        elif action in {"note.set", "note.clear"}:
            target = form.get("target", "user")
            if target not in {"user", "server"}:
                raise HTTPException(status_code=422, detail="invalid note target")
            if target == "server" and scope.guild_id is None:
                raise HTTPException(status_code=422, detail="DM scope has no server note")
            payload["target"] = target
            if action == "note.set":
                text = form.get("text", "").strip()
                if not 1 <= len(text) <= 1500:
                    raise HTTPException(status_code=422, detail="note must be 1..1500 characters")
                payload["text"] = text
            queue_target = scope.user_note if target == "user" else scope.realm

        else:
            raise HTTPException(status_code=422, detail="unsupported state admin action")

        command_id, _ = writer.enqueue(
            request_id=request_id,
            action=action,
            target=queue_target,
            payload=payload,
        )
        return RedirectResponse(
            url=_return_url(form, command_id),
            status_code=303,
        )

    return router
