from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..service import DashboardService


def build_router(service: DashboardService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/summaries", response_class=HTMLResponse)
    def summaries(
        request: Request,
        user_id: str = "",
        realm: str = "",
        q: str = "",
    ):
        data = service.summaries(user_id=user_id, realm=realm, query=q)
        return templates.TemplateResponse(
            request=request,
            name="summaries.html",
            context={"data": data},
        )

    return router
