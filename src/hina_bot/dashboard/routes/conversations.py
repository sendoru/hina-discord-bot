from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..service import DashboardService


def build_router(service: DashboardService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/conversations", response_class=HTMLResponse)
    def conversations(
        request: Request,
        page: int = 1,
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        q: str = "",
    ):
        data = service.conversations(
            page=page,
            scope=scope,
            realm=realm,
            user_id=user_id,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="conversations.html",
            context={"data": data},
        )

    return router
