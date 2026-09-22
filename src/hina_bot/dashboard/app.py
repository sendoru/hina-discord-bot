from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import DashboardSettings
from .repository import AdminRepository
from .routes import build_routers
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

    for router in build_routers(service, templates):
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
