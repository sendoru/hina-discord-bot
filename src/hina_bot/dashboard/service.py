from __future__ import annotations

from .analytics import build_analytics
from .identity import build_identity_observability
from .repository import AdminRepository
from .services import MemoryService, ReconciliationService, TraceService
from .telemetry import TelemetryReader


class DashboardService:
    """Compatibility facade over domain-specific dashboard read services."""

    def __init__(self, repository: AdminRepository, telemetry: TelemetryReader):
        self.repository = repository
        self.telemetry = telemetry
        self.traces_service = TraceService(repository, telemetry)
        self.memory_service = MemoryService(repository)
        self.reconciliation_service = ReconciliationService(repository, telemetry)

    def analytics(
        self,
        *,
        operation: str = "",
        model: str = "",
        provider: str = "",
        after: str = "",
        before: str = "",
    ) -> dict[str, object]:
        return build_analytics(
            self.telemetry.snapshot(),
            operation=operation,
            model=model,
            provider=provider,
            after=after,
            before=before,
        )

    def identity_observability(
        self,
        *,
        outcome: str = "",
        blocked_reason: str = "",
        after: str = "",
        before: str = "",
    ) -> dict[str, object]:
        return build_identity_observability(
            self.telemetry.snapshot(),
            outcome=outcome,
            blocked_reason=blocked_reason,
            after=after,
            before=before,
        )

    def overview(self) -> dict[str, object]:
        return self.traces_service.overview()

    def traces(self, **kwargs) -> dict[str, object]:
        return self.traces_service.traces(**kwargs)

    def trace(self, turn_id: str) -> dict[str, object] | None:
        return self.traces_service.trace(turn_id)

    def conversations(self, **kwargs) -> dict[str, object]:
        return self.traces_service.conversations(**kwargs)

    def memory_items(self, **kwargs) -> dict[str, object]:
        return self.memory_service.memory_items(**kwargs)

    def memory_item(self, item_id: int) -> dict[str, object] | None:
        return self.memory_service.memory_item(item_id)

    def summaries(self, **kwargs) -> dict[str, object]:
        return self.memory_service.summaries(**kwargs)

    def extraction_cursors(self, **kwargs) -> dict[str, object]:
        return self.memory_service.extraction_cursors(**kwargs)

    def reconciliation_proposals(self, **kwargs) -> dict[str, object]:
        return self.reconciliation_service.reconciliation_proposals(**kwargs)

    def reconciliation_proposal(self, proposal_id: int) -> dict[str, object] | None:
        return self.reconciliation_service.reconciliation_proposal(proposal_id)
