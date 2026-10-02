from __future__ import annotations

from ..filterutils import confidence_value, validate_filters
from ..repository import AdminRepository
from ..scopepicker import normalize_scope_filter
from ..scopepresenter import parse_scope_key, present_turn_scope
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
        value["origin_display"] = parse_scope_key(str(value.get("origin_realm") or ""))
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
        origin_scope_type: str = "",
        origin_guild_id: str = "",
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
        errors = validate_filters({
            "confidence_min": confidence_min,
            "confidence_max": confidence_max,
            "created_after": created_after,
            "created_before": created_before,
        }, self.timezone)

        origin_scope = normalize_scope_filter(
            scope_type=origin_scope_type,
            guild_id=origin_guild_id,
            channel_id=origin_channel_id,
            legacy_realm=origin_realm,
        )
        filters = {
            "user_id": user_id.strip(),
            "origin_realm": origin_scope.realm,
            "origin_realm_prefix": origin_scope.realm_prefix,
            "origin_channel_id": origin_scope.channel_id,
            "relation": relation.strip(),
            "kind": kind.strip(),
            "retry": retry.strip().lower(),
            "query": query.strip(),
            "confidence_min": confidence_value(confidence_min),
            "confidence_max": confidence_value(confidence_max),
            "created_after": db_utc_timestamp(created_after, self.timezone),
            "created_before": db_utc_timestamp(created_before, self.timezone),
        }
        total = 0 if errors else self.repository.count_reconciliation_proposals(**filters)
        pagination = self._page(page, page_size, total)
        if pagination.number > pagination.pages:
            pagination = Page(pagination.pages, pagination.size, pagination.total)
        rows = self.repository.search_reconciliation_proposals(
            **filters,
            limit=pagination.size,
            offset=(pagination.number - 1) * pagination.size,
        ) if not errors else []
        names = self.repository.latest_user_names()
        metadata = self.repository.discord_scope_metadata()
        proposal_rows = []
        for row in rows:
            value = self._proposal_row(row)
            value["user_name"] = names.get(
                (
                    str(value.get("origin_realm") or ""),
                    str(value.get("user_id") or ""),
                ),
                "",
            )
            self._decorate_scope_names(
                value,
                metadata,
                realm_key="origin_realm",
                channel_key="origin_channel_id",
            )
            proposal_rows.append(value)
        return {
            "filter_errors": errors,
            "rows": proposal_rows,
            "page": pagination,
            "stats": self.repository.reconciliation_stats(**filters) if not errors else {
                "total": 0, "average_confidence": None, "retry_suspects": 0,
                "relationship_proposals": 0, "relations": {},
            },
            "filters": {
                "user_id": user_id.strip(),
                "origin_scope_type": origin_scope.scope_type,
                "origin_guild_id": origin_scope.guild_id,
                "origin_realm": origin_scope.realm,
                "origin_channel_id": origin_scope.channel_id,
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
        names = self.repository.latest_user_names()
        metadata = self.repository.discord_scope_metadata()
        proposal["user_name"] = names.get(
            (
                str(proposal.get("origin_realm") or ""),
                str(proposal.get("user_id") or ""),
            ),
            "",
        )
        self._decorate_scope_names(
            proposal,
            metadata,
            realm_key="origin_realm",
            channel_key="origin_channel_id",
        )
        new_item_raw = self.repository.memory_item(int(proposal["new_memory_item_id"]))
        target_item_raw = self.repository.memory_item(int(proposal["target_memory_item_id"]))
        if new_item_raw is None or target_item_raw is None:
            return None
        new_item = self._memory_row(new_item_raw)
        target_item = self._memory_row(target_item_raw)
        for item in (new_item, target_item):
            self._decorate_scope_names(
                item,
                metadata,
                realm_key="origin_realm",
                channel_key="origin_channel_id",
            )

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
        source_by_id = {}
        for row in source_turns:
            value = dict(row)
            self._decorate_scope_names(value, metadata)
            value = present_turn_scope(value)
            source_by_id[str(value["message_id"])] = value
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
                self._decorate_scope_names(
                    self._memory_row(row),
                    metadata,
                    realm_key="origin_realm",
                    channel_key="origin_channel_id",
                )
                for row in self.repository.neighboring_memory_items(new_item_raw)
            ],
            "target_neighbors": [
                self._decorate_scope_names(
                    self._memory_row(row),
                    metadata,
                    realm_key="origin_realm",
                    channel_key="origin_channel_id",
                )
                for row in self.repository.neighboring_memory_items(target_item_raw)
            ],
            "extraction_traces": extraction_traces,
        }
