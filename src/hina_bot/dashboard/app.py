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

    @app.get("/memory", response_class=HTMLResponse)
    def memory_items(
        request: Request,
        page: int = 1,
        user_id: str = "",
        origin_realm: str = "",
        origin_channel_id: str = "",
        kind: str = "",
        disclosure: str = "",
        status: str = "",
        relationship: str = "",
        confidence_min: str = "",
        confidence_max: str = "",
        created_after: str = "",
        created_before: str = "",
        updated_after: str = "",
        updated_before: str = "",
        q: str = "",
    ):
        data = service.memory_items(
            page=page,
            user_id=user_id,
            origin_realm=origin_realm,
            origin_channel_id=origin_channel_id,
            kind=kind,
            disclosure=disclosure,
            status=status,
            relationship=relationship,
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="memory.html",
            context={"data": data},
        )

    @app.get("/memory/cursors", response_class=HTMLResponse)
    def memory_cursors(
        request: Request,
        user_id: str = "",
        realm: str = "",
        pending: str = "",
        q: str = "",
    ):
        data = service.extraction_cursors(
            user_id=user_id,
            realm=realm,
            pending=pending,
            query=q,
        )
        return templates.TemplateResponse(
            request=request,
            name="memory_cursors.html",
            context={"data": data},
        )

    @app.get("/memory/{item_id}", response_class=HTMLResponse)
    def memory_detail(request: Request, item_id: int):
        data = service.memory_item(item_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Memory item not found")
        return templates.TemplateResponse(
            request=request,
            name="memory_detail.html",
            context={"data": data},
        )

    @app.get("/reconciliation", response_class=HTMLResponse)
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

    @app.get("/reconciliation/{proposal_id}", response_class=HTMLResponse)
    def reconciliation_detail(request: Request, proposal_id: int):
        data = service.reconciliation_proposal(proposal_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Reconciliation proposal not found")
        return templates.TemplateResponse(
            request=request,
            name="reconciliation_detail.html",
            context={"data": data},
        )

    @app.get("/summaries", response_class=HTMLResponse)
    def summaries(
        request: Request,
        user_id: str = "",
        realm: str = "",
        q: str = "",
    ):
        data = service.summaries(user_id=user_id, realm=realm, query=q)
        return templates.TemplateResponse(
            request=request,
            name="summaries.html",
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
