from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..service import DashboardService


def build_router(service: DashboardService, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/reconciliation", response_class=HTMLResponse)
    def reconciliation(
        request: Request,
        page: int = 1,
        user_id: str = "",
        origin_realm: str = "",
        origin_channel_id: str = "",
        relation: str = "",
        kind: str = "",
        retry: str = "",
        confidence_min: str = "",
        confidence_max: str = "",
        created_after: str = "",
        created_before: str = "",
        q: str = "",
    ):
        data = service.reconciliation_proposals(
            page=page,
            user_id=user_id,
            origin_realm=origin_realm,
            origin_channel_id=origin_channel_id,
            relation=relation,
            kind=kind,
            retry=retry,
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            created_after=created_after,
            created_before=created_before,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="reconciliation.html",
            context={"data": data},
        )

    @router.get("/reconciliation/{proposal_id}", response_class=HTMLResponse)
    def reconciliation_detail(request: Request, proposal_id: int):
        data = service.reconciliation_proposal(proposal_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Reconciliation proposal not found")
        return templates.TemplateResponse(
            request=request,
            name="reconciliation_detail.html",
            context={"data": data},
        )

    return router
