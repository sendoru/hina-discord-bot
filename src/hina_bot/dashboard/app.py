from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import DashboardSettings
from .repository import AdminRepository
from .service import DashboardService
from .telemetry import TelemetryReader


def create_app(settings: DashboardSettings | None = None) -> FastAPI:
    settings = settings or DashboardSettings.load()
    repository = AdminRepository(settings.database_path)
    telemetry = TelemetryReader(settings.usage_log_path, settings.event_log_path)
    service = DashboardService(repository, telemetry)

    package_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(package_dir / "templates"))

    app = FastAPI(title="Hina Dashboard", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(package_dir / "static")), name="static")
    app.state.repository = repository
    app.state.telemetry = telemetry
    app.state.service = service

    @app.get("/healthz")
    def healthz():
        repository.ping()
        return {
            "status": "ok",
            "telemetry_sources": telemetry.source_status(),
        }

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="overview.html",
            context={"data": service.overview()},
        )

    @app.get("/traces", response_class=HTMLResponse)
    def traces(
        request: Request,
        page: int = 1,
        scope: str = "",
        status: str = "",
        tier: str = "",
        model: str = "",
        operation: str = "",
        error: str = "",
        web_search: str = "",
        after: str = "",
        before: str = "",
        q: str = "",
    ):
        data = service.traces(
            page=page,
            scope=scope,
            status=status,
            tier=tier,
            model=model,
            operation=operation,
            error=error,
            web_search=web_search,
            after=after,
            before=before,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="traces.html",
            context={"data": data},
        )

    @app.get("/traces/{turn_id}", response_class=HTMLResponse)
    def trace_detail(request: Request, turn_id: str):
        data = service.trace(turn_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Trace not found")
        return templates.TemplateResponse(
            request=request,
            name="trace_detail.html",
            context={"data": data},
        )

    @app.get("/conversations", response_class=HTMLResponse)
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

    return app


def main() -> None:
    import uvicorn

    try:
        settings = DashboardSettings.load()
        app = create_app(settings)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
