from __future__ import annotations

from .analytics import build_analytics
from .epochs import epoch_view, select_observability_epoch
from .identity import build_identity_observability
from .repository import AdminRepository
from .services import (
    ContextStateService,
    MemoryService,
    ReconciliationService,
    TraceService,
)
from .telemetry import TelemetryReader


class DashboardService:
    """Compatibility facade over domain-specific dashboard read services."""

    def __init__(
        self,
        repository: AdminRepository,
        telemetry: TelemetryReader,
        *,
        timezone: str = "Asia/Seoul",
    ):
        self.repository = repository
        self.telemetry = telemetry
        self.timezone = timezone
        self.traces_service = TraceService(repository, telemetry, timezone=timezone)
        self.memory_service = MemoryService(repository, timezone=timezone)
        self.context_state_service = ContextStateService(repository, timezone=timezone)
        self.reconciliation_service = ReconciliationService(
            repository,
            telemetry,
            timezone=timezone,
        )

    def analytics(
        self,
        *,
        operation: str = "",
        model: str = "",
        provider: str = "",
        after: str = "",
        before: str = "",
        epoch: str = "",
    ) -> dict[str, object]:
        selection = select_observability_epoch(
            self.telemetry.snapshot(),
            self.repository.observability_epochs(),
            epoch,
        )
        data = build_analytics(
            selection.snapshot,
            operation=operation,
            model=model,
            provider=provider,
            after=after,
            before=before,
            timezone=self.timezone,
        )
        data["epoch"] = epoch_view(selection)
        data["filters"]["epoch"] = selection.selected
        return data

    def identity_observability(
        self,
        *,
        outcome: str = "",
        blocked_reason: str = "",
        after: str = "",
        before: str = "",
        epoch: str = "",
    ) -> dict[str, object]:
        selection = select_observability_epoch(
            self.telemetry.snapshot(),
            self.repository.observability_epochs(),
            epoch,
        )
        data = build_identity_observability(
            selection.snapshot,
            outcome=outcome,
            blocked_reason=blocked_reason,
            after=after,
            before=before,
            timezone=self.timezone,
        )
        data["epoch"] = epoch_view(selection)
        data["filters"]["epoch"] = selection.selected
        return data

    def overview(self) -> dict[str, object]:
        return self.traces_service.overview()

    def traces(self, **kwargs) -> dict[str, object]:
        return self.traces_service.traces(**kwargs)

    def trace(self, turn_id: str) -> dict[str, object] | None:
        return self.traces_service.trace(turn_id)

    def conversations(self, **kwargs) -> dict[str, object]:
        return self.traces_service.conversations(**kwargs)

    def conversation_context(
        self,
        turn_row_id: int,
        **kwargs,
    ) -> dict[str, object] | None:
        return self.traces_service.conversation_context(turn_row_id, **kwargs)

    def context_state(self, **kwargs) -> dict[str, object]:
        return self.context_state_service.context_state(**kwargs)

    def memory_items(self, **kwargs) -> dict[str, object]:
        return self.memory_service.memory_items(**kwargs)

    def relationship_profiles(self, **kwargs) -> dict[str, object]:
        return self.memory_service.relationship_profiles(**kwargs)

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
