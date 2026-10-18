import hashlib
import json
import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from hina_bot.core.memory_items import (
    RELATIONSHIP_EVIDENCE_AXES,
    MemoryKind,
    validate_memory_item_fields,
)

from ..admin_write import AdminCommandWriter
from ..services import MemoryService


def build_router(
    service: MemoryService,
    writer: AdminCommandWriter,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    @router.get("/memory", response_class=HTMLResponse)
    def memory_items(
        request: Request,
        page: int = Query(1, ge=1),
        user_id: str = "",
        origin_scope_type: str = "",
        origin_guild_id: str = "",
        origin_realm: str = "",
        origin_channel_id: str = "",
        kind: str = "",
        disclosure: str = "",
        status: str = "",
        relationship: str = "",
        confidence_min: str = "",
        confidence_max: str = "",
        created_after: str = "",
        created_before: str = "",
        updated_after: str = "",
        updated_before: str = "",
        q: str = "",
    ):
        data = service.memory_items(
            page=page,
            user_id=user_id,
            origin_scope_type=origin_scope_type,
            origin_guild_id=origin_guild_id,
            origin_realm=origin_realm,
            origin_channel_id=origin_channel_id,
            kind=kind,
            disclosure=disclosure,
            status=status,
            relationship=relationship,
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="memory.html",
            context={"data": data},
        )

    @router.post("/memory/bulk-status")
    async def bulk_memory_status(request: Request):
        form = await writer.form(request)
        # Retraction is the only existing operator-controlled lifecycle transition.
        # Superseding requires reconciliation; reactivation is not supported.
        if form.get("status") != "retracted":
            raise HTTPException(status_code=422, detail="only retracted status is supported")
        if form.get("confirm") != "yes":
            raise HTTPException(status_code=422, detail="retract confirmation is required")
        request_id = form.get("_request_id", "")
        if not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise HTTPException(status_code=422, detail="invalid admin request id")
        try:
            items = json.loads(form.get("items", ""))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid memory selection") from exc
        if not isinstance(items, list) or not 1 <= len(items) <= 50:
            raise HTTPException(status_code=422, detail="select between 1 and 50 memory items")

        results = []
        seen = set()
        for selected in items:
            item_id = selected.get("item_id") if isinstance(selected, dict) else None
            revision = selected.get("expected_revision") if isinstance(selected, dict) else None
            result = {"item_id": item_id, "status": "failed"}
            results.append(result)
            if type(item_id) is not int or item_id <= 0:
                result["error"] = "invalid memory item id"
                continue
            if item_id in seen:
                result["error"] = "duplicate memory item"
                continue
            seen.add(item_id)
            if type(revision) is not int or revision < 0:
                result["error"] = "invalid memory revision"
                continue
            # One independently tracked command per row reuses runtime locking,
            # revision validation, auditing, and failure isolation. Retries of the
            # same submission reuse the same command instead of applying it twice.
            child_id = hashlib.sha256(
                f"{request_id}:retracted:{item_id}:{revision}".encode()
            ).hexdigest()[:32]
            existing_id = service.repository.admin_command_id_for_request(child_id)
            if existing_id is not None:
                result.update(status="pending", command_id=existing_id)
                continue
            item = service.repository.memory_item(item_id)
            if item is None:
                result["error"] = "Memory item not found"
                continue
            if item["status"] != "active":
                result["error"] = "only active memory items can be retracted"
                continue
            if item["revision"] != revision:
                result["error"] = "memory item changed; reload before retracting"
                continue
            try:
                command_id, _ = writer.enqueue(
                    request_id=child_id,
                    action="memory.item.retract",
                    target=str(item_id),
                    payload={"item_id": item_id, "expected_revision": revision},
                )
            except HTTPException as exc:
                result["error"] = str(exc.detail)
                continue
            result.update(status="pending", command_id=command_id)
        return {"results": results}

    @router.get("/memory/cursors", response_class=HTMLResponse)
    def memory_cursors(
        request: Request,
        user_id: str = "",
        realm: str = "",
        pending: str = "",
        q: str = "",
    ):
        data = service.extraction_cursors(
            user_id=user_id,
            realm=realm,
            pending=pending,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="memory_cursors.html",
            context={"data": data},
        )

    @router.get("/memory/{item_id}", response_class=HTMLResponse)
    def memory_detail(request: Request, item_id: int, queued: int | None = None):
        data = service.memory_item(item_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Memory item not found")
        data["queued"] = queued
        return templates.TemplateResponse(
            request=request,
            name="memory_detail.html",
            context={"data": data},
        )

    @router.post("/memory/{item_id}/edit")
    async def edit_memory_item(request: Request, item_id: int):
        form = await writer.form(request)
        data = service.memory_item(item_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Memory item not found")
        item = data["item"]
        if item["status"] != "active":
            raise HTTPException(status_code=409, detail="only active memory items can be edited")

        try:
            expected_revision = int(form.get("expected_revision", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid memory revision") from exc
        if expected_revision < 0:
            raise HTTPException(status_code=422, detail="invalid memory revision")
        if item["revision"] != expected_revision:
            raise HTTPException(
                status_code=409, detail="memory item changed; reload before editing"
            )

        kind = form.get("kind", "")
        relationship_evidence: dict[str, int] = {}
        if kind == MemoryKind.RELATIONSHIP.value:
            for axis in RELATIONSHIP_EVIDENCE_AXES:
                raw_value = form.get(f"relationship_evidence_{axis}", "").strip()
                if not raw_value:
                    continue
                try:
                    value = int(raw_value)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"invalid relationship evidence: {axis}",
                    ) from exc
                if value:
                    relationship_evidence[axis] = value

        try:
            fields = validate_memory_item_fields(
                form.get("content", ""),
                kind=kind,
                disclosure=form.get("disclosure", ""),
                confidence=form.get("confidence", ""),
                relationship_evidence=relationship_evidence,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        payload = {
            "item_id": item_id,
            "expected_revision": expected_revision,
            "content": fields.content,
            "kind": fields.kind.value,
            "disclosure": fields.disclosure.value,
            "confidence": fields.confidence,
            "relationship_evidence": fields.relationship_evidence.as_dict(),
        }
        command_id, _ = writer.enqueue(
            request_id=form.get("_request_id", ""),
            action="memory.item.edit",
            target=str(item_id),
            payload=payload,
        )
        return RedirectResponse(
            url=f"/memory/{item_id}?queued={command_id}",
            status_code=303,
        )

    @router.post("/memory/{item_id}/retract")
    async def retract_memory_item(request: Request, item_id: int):
        form = await writer.form(request)
        if form.get("confirm") != "yes":
            raise HTTPException(status_code=422, detail="retract confirmation is required")
        data = service.memory_item(item_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Memory item not found")
        item = data["item"]
        if item["status"] != "active":
            raise HTTPException(status_code=409, detail="only active memory items can be retracted")

        try:
            expected_revision = int(form.get("expected_revision", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid memory revision") from exc
        if expected_revision < 0:
            raise HTTPException(status_code=422, detail="invalid memory revision")
        if item["revision"] != expected_revision:
            raise HTTPException(
                status_code=409, detail="memory item changed; reload before retracting"
            )

        command_id, _ = writer.enqueue(
            request_id=form.get("_request_id", ""),
            action="memory.item.retract",
            target=str(item_id),
            payload={
                "item_id": item_id,
                "expected_revision": expected_revision,
            },
        )
        return RedirectResponse(
            url=f"/memory/{item_id}?queued={command_id}",
            status_code=303,
        )

    return router
