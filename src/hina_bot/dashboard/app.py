from __future__ import annotations

from fastapi import FastAPI

from .config import DashboardSettings
from .repository import AdminRepository
from .telemetry import TelemetryReader


def create_app(settings: DashboardSettings | None = None) -> FastAPI:
    settings = settings or DashboardSettings.load()
    repository = AdminRepository(settings.database_path)
    telemetry = TelemetryReader(settings.usage_log_path, settings.event_log_path)

    app = FastAPI(title="Hina Dashboard", docs_url=None, redoc_url=None)
    app.state.repository = repository
    app.state.telemetry = telemetry

    @app.get("/healthz")
    def healthz():
        repository.ping()
        return {
            "status": "ok",
            "telemetry_sources": telemetry.source_status(),
        }

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
