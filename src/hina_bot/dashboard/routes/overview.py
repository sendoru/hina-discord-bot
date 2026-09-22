from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..service import DashboardService


def build_router(service: DashboardService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="overview.html",
            context={"data": service.overview()},
        )

    return router
