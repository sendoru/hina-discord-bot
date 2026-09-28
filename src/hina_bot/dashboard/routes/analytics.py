from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..analytics import build_analytics
from ..epochs import epoch_view, select_observability_epoch
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

    @router.get("/analytics", response_class=HTMLResponse)
    def analytics(
        request: Request,
        operation: str = "",
        model: str = "",
        provider: str = "",
        after: str = "",
        before: str = "",
        epoch: str = "",
    ):
        selection = select_observability_epoch(
            telemetry.snapshot(),
            repository.observability_epochs(),
            epoch,
        )
        data = build_analytics(
            selection.snapshot,
            operation=operation,
            model=model,
            provider=provider,
            after=after,
            before=before,
            timezone=timezone,
        )
        data["epoch"] = epoch_view(selection)
        data["filters"]["epoch"] = selection.selected
        return templates.TemplateResponse(
            request=request,
            name="analytics.html",
            context={"data": data},
        )

    return router
