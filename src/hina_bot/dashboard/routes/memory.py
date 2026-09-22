from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..service import DashboardService


def build_router(service: DashboardService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/memory", response_class=HTMLResponse)
    def memory_items(
        request: Request,
        page: int = 1,
        user_id: str = "",
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
    def memory_detail(request: Request, item_id: int):
        data = service.memory_item(item_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Memory item not found")
        return templates.TemplateResponse(
            request=request,
            name="memory_detail.html",
            context={"data": data},
        )

    return router
