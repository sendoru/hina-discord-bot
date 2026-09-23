from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from ..service import DashboardService
from . import (
    analytics,
    conversations,
    identity,
    memory,
    overview,
    reconciliation,
    relationships,
    summaries,
    traces,
)


def build_routers(
    service: DashboardService,
    templates: Jinja2Templates,
) -> tuple[APIRouter, ...]:
    return (
        overview.build_router(service, templates),
        analytics.build_router(service, templates),
        identity.build_router(service, templates),
        traces.build_router(service, templates),
        conversations.build_router(service, templates),
        memory.build_router(service, templates),
        relationships.build_router(service, templates),
        reconciliation.build_router(service, templates),
        summaries.build_router(service, templates),
    )
