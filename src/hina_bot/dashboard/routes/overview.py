from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..services import ContextStateService, TraceService


def build_router(
    service: TraceService,
    context_state_service: ContextStateService,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="overview.html",
            context={
                "data": service.overview(),
                "global_policy": context_state_service.global_policy(),
            },
        )

    return router
