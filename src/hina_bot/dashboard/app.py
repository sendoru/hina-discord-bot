from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import DashboardSettings
from .filterutils import applied_filters, remove_filter_url
from .navigation import back_url, detail_url
from .repository import AdminRepository
from .routes import build_routers
from .services import (
    ContextStateService,
    MemoryService,
    ReconciliationService,
    TraceService,
)
from .telemetry import TelemetryReader
from .timeutils import filter_input_time, filter_input_type, format_local_time, telemetry_freshness


def create_app(settings: DashboardSettings | None = None) -> FastAPI:
    settings = settings or DashboardSettings.load()
    repository = AdminRepository(settings.database_path)
    telemetry = TelemetryReader(settings.usage_log_path, settings.event_log_path)
    trace_service = TraceService(repository, telemetry, timezone=settings.timezone)
    memory_service = MemoryService(repository, timezone=settings.timezone)
    context_state_service = ContextStateService(repository, timezone=settings.timezone)
    reconciliation_service = ReconciliationService(
        repository,
        telemetry,
        timezone=settings.timezone,
    )

    package_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(package_dir / "templates"))
    templates.env.filters["localtime"] = (
        lambda value: format_local_time(value, settings.timezone)
    )
    templates.env.filters["filtertype"] = lambda value: filter_input_type(value, settings.timezone)
    templates.env.filters["filtertime"] = lambda value: filter_input_time(value, settings.timezone)
    templates.env.globals["back_url"] = back_url
    templates.env.globals["detail_url"] = detail_url
    templates.env.globals["applied_filters"] = applied_filters
    templates.env.globals["remove_filter_url"] = remove_filter_url
    templates.env.globals["dashboard_timezone"] = settings.timezone
    templates.env.globals["telemetry_freshness"] = telemetry_freshness

    app = FastAPI(title="Hina Dashboard", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(package_dir / "static")), name="static")
    app.state.repository = repository
    app.state.telemetry = telemetry

    def html_page_request(request: Request) -> bool:
        accept = request.headers.get("accept", "")
        return (request.url.path != "/healthz"
                and not request.url.path.startswith("/static/")
                and ("text/html" in accept or not accept or accept == "*/*"))

    def error_page(request: Request, status: int):
        path = request.url.path
        default = next((item for item in (
            "/memory/cursors", "/traces", "/memory", "/reconciliation", "/conversations",
            "/analytics", "/identity", "/summaries", "/relationships", "/state",
        ) if path == item or path.startswith(item + "/")), "/")
        return templates.TemplateResponse(
            request=request, name="error.html", status_code=status,
            context={"status": status, "return_url": back_url(request, default)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def html_http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and html_page_request(request):
            return error_page(request, 404)
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def html_validation_error(request: Request, exc: RequestValidationError):
        if html_page_request(request):
            return error_page(request, 422)
        return await request_validation_exception_handler(request, exc)

    @app.get("/healthz")
    def healthz():
        repository.ping()
        return {
            "status": "ok",
            "telemetry_sources": telemetry.source_status(),
        }

    for router in build_routers(
        repository=repository,
        telemetry=telemetry,
        trace_service=trace_service,
        memory_service=memory_service,
        context_state_service=context_state_service,
        reconciliation_service=reconciliation_service,
        templates=templates,
        timezone=settings.timezone,
    ):
        app.include_router(router)

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
