from __future__ import annotations

from ..repository import AdminRepository
from ..telemetry import TelemetryReader
from ..timeutils import db_utc_timestamp
from .base import Page, _decode_json
from .memory import MemoryService


class ReconciliationService(MemoryService):
    def __init__(
        self,
        repository: AdminRepository,
        telemetry: TelemetryReader,
        *,
        timezone: str = "Asia/Seoul",
    ):
        super().__init__(repository, timezone=timezone)
        self.telemetry = telemetry

    @staticmethod
    def _proposal_row(row: dict[str, object]) -> dict[str, object]:
        value = dict(row)
        for prefix in ("", "new_", "target_"):
            key = f"{prefix}source_message_ids"
            decoded_key = f"{prefix}source_message_ids_decoded"
            raw = _decode_json(value.get(key), [])
            value[decoded_key] = (
                tuple(str(item) for item in raw) if isinstance(raw, list) else ()
            )
        for prefix in ("new_", "target_"):
            key = f"{prefix}relationship_evidence"
            raw = _decode_json(value.get(key), {})
            value[f"{prefix}relationship_evidence_decoded"] = (
                raw if isinstance(raw, dict) else {}
            )
        value["retry_suspect"] = bool(value.get("retry_suspect"))
        new_sources = set(value["new_source_message_ids_decoded"])
        target_sources = set(value["target_source_message_ids_decoded"])
        value["source_overlap_ids"] = tuple(sorted(new_sources & target_sources))
        value["same_source_set"] = bool(new_sources) and new_sources == target_sources
        return value

    def reconciliation_proposals(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        user_id: str = "",
        origin_realm: str = "",
        origin_channel_id: str = "",
        relation: str = "",
        kind: str = "",
        retry: str = "",
        query: str = "",
        confidence_min: str = "",
        confidence_max: str = "",
        created_after: str = "",
        created_before: str = "",
    ) -> dict[str, object]:
        def optional_float(value: str) -> float | None:
            text = value.strip()
            if not text:
                return None
            try:
                return min(1.0, max(0.0, float(text)))
            except ValueError:
                return None

        filters = {
            "user_id": user_id.strip(),
            "origin_realm": origin_realm.strip(),
            "origin_channel_id": origin_channel_id.strip(),
            "relation": relation.strip(),
            "kind": kind.strip(),
            "retry": retry.strip().lower(),
            "query": query.strip(),
            "confidence_min": optional_float(confidence_min),
            "confidence_max": optional_float(confidence_max),
            "created_after": db_utc_timestamp(created_after, self.timezone),
            "created_before": db_utc_timestamp(created_before, self.timezone),
        }
        total = self.repository.count_reconciliation_proposals(**filters)
        pagination = self._page(page, page_size, total)
        if pagination.number > pagination.pages:
            pagination = Page(pagination.pages, pagination.size, pagination.total)
        rows = self.repository.search_reconciliation_proposals(
            **filters,
            limit=pagination.size,
            offset=(pagination.number - 1) * pagination.size,
        )
        return {
            "rows": [self._proposal_row(row) for row in rows],
            "page": pagination,
            "stats": self.repository.reconciliation_stats(**filters),
            "filters": {
                "user_id": user_id.strip(),
                "origin_realm": origin_realm.strip(),
                "origin_channel_id": origin_channel_id.strip(),
                "relation": relation.strip(),
                "kind": kind.strip(),
                "retry": retry.strip().lower(),
                "q": query.strip(),
                "confidence_min": confidence_min.strip(),
                "confidence_max": confidence_max.strip(),
                "created_after": created_after.strip(),
                "created_before": created_before.strip(),
            },
            "schema": self.repository.reconciliation_schema(),
            "time_ranges": self.time_ranges(),
        }

    def reconciliation_proposal(self, proposal_id: int) -> dict[str, object] | None:
        raw = self.repository.reconciliation_proposal(proposal_id)
        if raw is None:
            return None
        proposal = self._proposal_row(raw)
        new_item_raw = self.repository.memory_item(int(proposal["new_memory_item_id"]))
        target_item_raw = self.repository.memory_item(int(proposal["target_memory_item_id"]))
        if new_item_raw is None or target_item_raw is None:
            return None
        new_item = self._memory_row(new_item_raw)
        target_item = self._memory_row(target_item_raw)

        all_source_ids = tuple(
            dict.fromkeys(
                (
                    *proposal["source_message_ids_decoded"],
                    *new_item["source_message_ids_decoded"],
                    *target_item["source_message_ids_decoded"],
                )
            )
        )
        source_turns = self.repository.turns_for_message_ids(list(all_source_ids))
        source_by_id = {str(row["message_id"]): row for row in source_turns}
        sources = [
            {"message_id": message_id, "turn": source_by_id.get(message_id)}
            for message_id in all_source_ids
        ]

        extraction_traces = []
        seen_turn_ids: set[str] = set()
        new_source_ids = set(new_item["source_message_ids_decoded"])
        for source in sources:
            turn = source["turn"]
            if not turn or str(source["message_id"]) not in new_source_ids:
                continue
            turn_id = turn.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id or turn_id in seen_turn_ids:
                continue
            seen_turn_ids.add(turn_id)
            trace = self.telemetry.for_turn(turn_id)
            memory_usage = tuple(
                row
                for row in trace.usage
                if row.get("operation")
                in {"extract_memory_items_shadow", "memory.shadow_extraction"}
            )
            extraction_traces.append(
                {
                    "turn_id": turn_id,
                    "usage": memory_usage,
                    "events": trace.events,
                    "exchange": trace.exchanges[-1] if trace.exchanges else None,
                }
            )

        return {
            "proposal": proposal,
            "new_item": new_item,
            "target_item": target_item,
            "sources": sources,
            "new_neighbors": [
                self._memory_row(row)
                for row in self.repository.neighboring_memory_items(new_item_raw)
            ],
            "target_neighbors": [
                self._memory_row(row)
                for row in self.repository.neighboring_memory_items(target_item_raw)
            ],
            "extraction_traces": extraction_traces,
        }
