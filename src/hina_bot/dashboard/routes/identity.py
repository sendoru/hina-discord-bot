from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..epochs import epoch_view, select_observability_epoch
from ..identity import build_identity_observability
from ..repository import AdminRepository
from ..telemetry import TelemetryReader


def build_router(
    repository: AdminRepository,
    telemetry: TelemetryReader,
    templates: Jinja2Templates,
    *,
    timezone: str,
) -> APIRouter:
    router = APIRouter()

    @router.get("/identity", response_class=HTMLResponse)
    def identity_observability(
        request: Request,
        outcome: str = "",
        blocked_reason: str = "",
        after: str = "",
        before: str = "",
        epoch: str = "",
    ):
        selection = select_observability_epoch(
            telemetry.snapshot(),
            repository.observability_epochs(),
            epoch,
        )
        data = build_identity_observability(
            selection.snapshot,
            outcome=outcome,
            blocked_reason=blocked_reason,
            after=after,
            before=before,
            timezone=timezone,
        )
        data["epoch"] = epoch_view(selection)
        data["filters"]["epoch"] = selection.selected
        return templates.TemplateResponse(
            request=request,
            name="identity.html",
            context={"data": data},
        )

    return router
