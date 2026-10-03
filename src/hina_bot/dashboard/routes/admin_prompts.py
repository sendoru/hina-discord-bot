from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from hina_bot.core.runtime_knowledge import KNOWLEDGE_LEVELS

from ..admin_write import AdminCommandWriter
from ..repository import AdminRepository


def _decode_list(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded if isinstance(item, str)]


def _read_model(repository: AdminRepository) -> dict[str, object]:
    instructions = []
    for row in repository.instruction_rows():
        instructions.append({**row, "enabled": bool(row.get("enabled"))})

    knowledge = []
    for row in repository.runtime_knowledge_rows():
        knowledge.append(
            {
                **row,
                "enabled": bool(row.get("enabled")),
                "keywords_list": _decode_list(row.get("keywords")),
                "subjects_list": _decode_list(row.get("subjects")),
            }
        )
    actions = [
        row
        for row in repository.admin_command_rows(limit=80)
        if str(row.get("action") or "").startswith(("instruction.", "knowledge."))
    ][:40]
    return {
        "instructions": instructions,
        "knowledge": knowledge,
        "actions": actions,
        "awareness_levels": sorted(KNOWLEDGE_LEVELS),
    }


def _redirect(command_id: int) -> RedirectResponse:
    return RedirectResponse(
        url="/admin/prompts?" + urlencode({"queued": str(command_id)}),
        status_code=303,
    )


def build_router(
    repository: AdminRepository,
    writer: AdminCommandWriter,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/prompts", response_class=HTMLResponse)
    def prompt_state(request: Request, queued: int | None = None):
        data = _read_model(repository)
        data["queued"] = queued
        return templates.TemplateResponse(
            request=request,
            name="admin_prompts.html",
            context={"data": data},
        )

    @router.post("/admin/prompts/instruction")
    async def instruction_action(request: Request):
        form = await writer.form(request)
        action = form.get("action", "")
        identifier = form.get("id", "").strip()
        request_id = form.get("_request_id", "")
        if action not in {"add", "edit", "enable", "disable", "remove"}:
            raise HTTPException(status_code=422, detail="unsupported instruction action")
        if not identifier:
            raise HTTPException(status_code=422, detail="instruction id is required")
        if action == "remove" and form.get("confirm") != "yes":
            raise HTTPException(status_code=422, detail="remove confirmation is required")

        payload: dict[str, object] = {"id": identifier}
        queue_action = f"instruction.{action}"
        if action in {"add", "edit"}:
            payload["text"] = form.get("text", "")
        elif action in {"enable", "disable"}:
            queue_action = "instruction.state"
            payload["enabled"] = action == "enable"

        command_id, _ = writer.enqueue(
            request_id=request_id,
            action=queue_action,
            target=identifier,
            payload=payload,
        )
        return _redirect(command_id)

    @router.post("/admin/prompts/knowledge")
    async def knowledge_action(request: Request):
        form = await writer.form(request)
        action = form.get("action", "")
        kind = form.get("kind", "")
        identifier = form.get("id", "").strip()
        request_id = form.get("_request_id", "")
        if action not in {"add", "edit", "enable", "disable", "remove"}:
            raise HTTPException(status_code=422, detail="unsupported knowledge action")
        if kind not in {"world_fact", "interpretation"}:
            raise HTTPException(status_code=422, detail="invalid knowledge kind")
        if not identifier:
            raise HTTPException(status_code=422, detail="knowledge id is required")
        if action == "remove" and form.get("confirm") != "yes":
            raise HTTPException(status_code=422, detail="remove confirmation is required")

        payload: dict[str, object] = {"id": identifier, "kind": kind}
        queue_action = f"knowledge.{action}"
        if action in {"add", "edit"}:
            awareness = form.get("awareness", "")
            if awareness not in KNOWLEDGE_LEVELS:
                raise HTTPException(status_code=422, detail="invalid knowledge awareness")
            payload.update(
                {
                    "content": form.get("content", ""),
                    "keywords": form.get("keywords", ""),
                    "subjects": form.get("subjects", ""),
                    "awareness": awareness,
                    "timeline": form.get("timeline", ""),
                }
            )
        elif action in {"enable", "disable"}:
            queue_action = "knowledge.state"
            payload["enabled"] = action == "enable"

        command_id, _ = writer.enqueue(
            request_id=request_id,
            action=queue_action,
            target=f"{kind}:{identifier}",
            payload=payload,
        )
        return _redirect(command_id)

    return router
