from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..services import ContextStateService


def build_router(service: ContextStateService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/state", response_class=HTMLResponse)
    def context_state(
        request: Request,
        target_scope_type: str = "",
        target_guild_id: str = "",
        target_channel_id: str = "",
        target_user_id: str = "",
        q: str = "",
    ):
        data = service.context_state(
            target_scope_type=target_scope_type,
            target_guild_id=target_guild_id,
            target_channel_id=target_channel_id,
            target_user_id=target_user_id,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="state.html",
            context={"data": data},
        )

    return router
