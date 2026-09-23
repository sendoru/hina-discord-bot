from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..service import DashboardService


def build_router(service: DashboardService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/relationships", response_class=HTMLResponse)
    def relationship_profiles(
        request: Request,
        target_guild_id: str = "",
        target_channel_id: str = "",
        q: str = "",
    ):
        data = service.relationship_profiles(
            target_guild_id=target_guild_id,
            target_channel_id=target_channel_id,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="relationships.html",
            context={"data": data},
        )

    return router
