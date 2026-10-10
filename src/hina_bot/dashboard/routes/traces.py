from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..services import TraceService


def build_router(service: TraceService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/traces", response_class=HTMLResponse)
    def traces(
        request: Request,
        page: int = Query(1, ge=1),
        scope: str = "",
        status: str = "",
        tier: str = "",
        model: str = "",
        operation: str = "",
        error: str = "",
        issue: str = "",
        web_search: str = "",
        memory_failure: str = "",
        after: str = "",
        before: str = "",
        q: str = "",
        epoch: str = "",
    ):
        data = service.traces(
            page=page,
            scope=scope,
            status=status,
            tier=tier,
            model=model,
            operation=operation,
            error=error,
            issue=issue,
            web_search=web_search,
            memory_failure=memory_failure,
            after=after,
            before=before,
            query=q,
            epoch=epoch,
        )
        return templates.TemplateResponse(
            request=request,
            name="traces.html",
            context={"data": data},
        )

    @router.get("/traces/{turn_id}", response_class=HTMLResponse)
    def trace_detail(request: Request, turn_id: str):
        data = service.trace(turn_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Trace not found")
        return templates.TemplateResponse(
            request=request,
            name="trace_detail.html",
            context={"data": data},
        )

    return router
