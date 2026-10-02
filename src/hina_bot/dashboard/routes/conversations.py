from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..services import TraceService


def build_router(service: TraceService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/conversations", response_class=HTMLResponse)
    def conversations(
        request: Request,
        page: int = Query(1, ge=1),
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        after: str = "",
        before: str = "",
        q: str = "",
    ):
        data = service.conversations(
            page=page,
            scope=scope,
            realm=realm,
            user_id=user_id,
            after=after,
            before=before,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="conversations.html",
            context={"data": data},
        )

    @router.get("/conversations/{turn_row_id}/context", response_class=HTMLResponse)
    def conversation_context(
        request: Request,
        turn_row_id: int,
        before: int = 5,
        after: int = 5,
    ):
        data = service.conversation_context(
            turn_row_id,
            before=before,
            after=after,
        )
        if data is None:
            raise HTTPException(status_code=404, detail="Conversation turn not found")
        return templates.TemplateResponse(
            request=request,
            name="conversation_context.html",
            context={"data": data},
        )

    return router
