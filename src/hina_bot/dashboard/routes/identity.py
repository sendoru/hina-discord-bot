from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..service import DashboardService


def build_router(service: DashboardService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/identity", response_class=HTMLResponse)
    def identity_observability(
        request: Request,
        outcome: str = "",
        blocked_reason: str = "",
        after: str = "",
        before: str = "",
    ):
        data = service.identity_observability(
            outcome=outcome,
            blocked_reason=blocked_reason,
            after=after,
            before=before,
        )
        return templates.TemplateResponse(
            request=request,
            name="identity.html",
            context={"data": data},
        )

    return router
