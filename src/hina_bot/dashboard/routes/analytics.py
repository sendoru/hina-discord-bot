from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..service import DashboardService


def build_router(service: DashboardService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/analytics", response_class=HTMLResponse)
    def analytics(
        request: Request,
        operation: str = "",
        model: str = "",
        provider: str = "",
        after: str = "",
        before: str = "",
    ):
        data = service.analytics(
            operation=operation,
            model=model,
            provider=provider,
            after=after,
            before=before,
        )
        return templates.TemplateResponse(
            request=request,
            name="analytics.html",
            context={"data": data},
        )

    return router
