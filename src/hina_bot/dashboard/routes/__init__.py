from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from ..admin_write import AdminCommandWriter
from ..repository import AdminRepository
from ..services import (
    ContextStateService,
    MemoryService,
    ReconciliationService,
    TraceService,
)
from ..telemetry import TelemetryReader
from . import (
    admin_prompts,
    admin_runtime,
    analytics,
    conversations,
    memory,
    overview,
    reconciliation,
    relationships,
    state,
    summaries,
    traces,
)


def build_routers(
    *,
    repository: AdminRepository,
    admin_writer: AdminCommandWriter,
    telemetry: TelemetryReader,
    trace_service: TraceService,
    memory_service: MemoryService,
    context_state_service: ContextStateService,
    reconciliation_service: ReconciliationService,
    templates: Jinja2Templates,
    timezone: str,
) -> tuple[APIRouter, ...]:
    return (
        admin_runtime.build_router(repository, admin_writer, templates),
        admin_prompts.build_router(repository, admin_writer, templates),
        overview.build_router(trace_service, templates),
        analytics.build_router(
            repository,
            telemetry,
            templates,
            timezone=timezone,
        ),
        traces.build_router(trace_service, templates),
        conversations.build_router(trace_service, templates),
        memory.build_router(memory_service, admin_writer, templates),
        relationships.build_router(memory_service, templates),
        state.build_router(context_state_service, admin_writer, templates),
        reconciliation.build_router(reconciliation_service, templates),
        summaries.build_router(memory_service, templates),
    )
