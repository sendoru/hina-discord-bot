from __future__ import annotations

from .base import Page, ReadService, _db_timestamp, _decode_json


class MemoryService(ReadService):
    @staticmethod
    def _memory_row(row: dict[str, object]) -> dict[str, object]:
        value = dict(row)
        sources = _decode_json(value.get("source_message_ids"), [])
        evidence = _decode_json(value.get("relationship_evidence"), {})
        value["source_message_ids_decoded"] = (
            tuple(str(item) for item in sources) if isinstance(sources, list) else ()
        )
        value["relationship_evidence_decoded"] = (
            evidence if isinstance(evidence, dict) else {}
        )
        value["has_relationship_evidence"] = bool(
            value["relationship_evidence_decoded"]
        )
        value["status"] = value.get("status")
        value["superseded_by"] = value.get("superseded_by")
        return value

    def memory_items(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        user_id: str = "",
        origin_realm: str = "",
        origin_channel_id: str = "",
        kind: str = "",
        disclosure: str = "",
        status: str = "",
        relationship: str = "",
        query: str = "",
        confidence_min: str = "",
        confidence_max: str = "",
        created_after: str = "",
        created_before: str = "",
        updated_after: str = "",
        updated_before: str = "",
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
            "kind": kind.strip(),
            "disclosure": disclosure.strip(),
            "status": status.strip(),
            "relationship": relationship.strip(),
            "query": query.strip(),
            "confidence_min": optional_float(confidence_min),
            "confidence_max": optional_float(confidence_max),
            "created_after": _db_timestamp(created_after),
            "created_before": _db_timestamp(created_before),
            "updated_after": _db_timestamp(updated_after),
            "updated_before": _db_timestamp(updated_before),
        }
        total = self.repository.count_memory_items(**filters)
        pagination = self._page(page, page_size, total)
        if pagination.number > pagination.pages:
            pagination = Page(pagination.pages, pagination.size, pagination.total)
        rows = self.repository.search_memory_items(
            **filters,
            limit=pagination.size,
            offset=(pagination.number - 1) * pagination.size,
        )
        return {
            "rows": [self._memory_row(row) for row in rows],
            "page": pagination,
            "filters": {
                "user_id": user_id.strip(),
                "origin_realm": origin_realm.strip(),
                "origin_channel_id": origin_channel_id.strip(),
                "kind": kind.strip(),
                "disclosure": disclosure.strip(),
                "status": status.strip(),
                "relationship": relationship.strip(),
                "q": query.strip(),
                "confidence_min": confidence_min.strip(),
                "confidence_max": confidence_max.strip(),
                "created_after": created_after.strip(),
                "created_before": created_before.strip(),
                "updated_after": updated_after.strip(),
                "updated_before": updated_before.strip(),
            },
            "schema": self.repository.memory_schema(),
        }

    def memory_item(self, item_id: int) -> dict[str, object] | None:
        raw = self.repository.memory_item(item_id)
        if raw is None:
            return None
        item = self._memory_row(raw)
        source_ids = list(item["source_message_ids_decoded"])
        source_turns = self.repository.turns_for_message_ids(source_ids)
        source_by_id = {str(row["message_id"]): row for row in source_turns}
        sources = [
            {
                "message_id": message_id,
                "turn": source_by_id.get(message_id),
            }
            for message_id in source_ids
        ]
        neighbors = [
            self._memory_row(row)
            for row in self.repository.neighboring_memory_items(raw)
        ]
        return {
            "item": item,
            "sources": sources,
            "neighbors": neighbors,
            "schema": self.repository.memory_schema(),
        }

    def summaries(
        self,
        *,
        user_id: str = "",
        realm: str = "",
        query: str = "",
    ) -> dict[str, object]:
        user_id = user_id.strip()
        realm = realm.strip()
        query = query.strip().lower()
        counts = self.repository.memory_counts_by_user()
        cursor_rows = self.repository.extraction_cursor_status()
        cursor_by_scope = {str(row["scope"]): row for row in cursor_rows}

        def keep(row: dict[str, object]) -> bool:
            if user_id and str(row.get("user_id", "")) != user_id:
                return False
            if realm and str(row.get("realm", "")) != realm:
                return False
            if query:
                haystack = " ".join(
                    (
                        str(row.get("scope", "")),
                        str(row.get("user_id", "")),
                        str(row.get("name", "")),
                        str(row.get("text", "")),
                    )
                ).lower()
                if query not in haystack:
                    return False
            return True

        personal = []
        for row in self.repository.personal_summary_status():
            if not keep(row):
                continue
            value = dict(row)
            value["structured_memory_count"] = counts.get(str(row["user_id"]), 0)
            value["extraction_cursor"] = cursor_by_scope.get(str(row["scope"]))
            personal.append(value)

        shared = [
            dict(row)
            for row in self.repository.shared_summary_status()
            if keep(row)
        ]
        return {
            "personal": personal,
            "shared": shared,
            "filters": {"user_id": user_id, "realm": realm, "q": query},
        }

    def extraction_cursors(
        self,
        *,
        user_id: str = "",
        realm: str = "",
        pending: str = "",
        query: str = "",
    ) -> dict[str, object]:
        user_id = user_id.strip()
        realm = realm.strip()
        pending = pending.strip().lower()
        query = query.strip().lower()
        rows: list[dict[str, object]] = []
        for raw in self.repository.extraction_cursor_status():
            row = dict(raw)
            row["cursor_delta"] = None
            extraction = row.get("extraction_through_id")
            summary = row.get("summary_through_id")
            if isinstance(extraction, int) and isinstance(summary, int):
                row["cursor_delta"] = extraction - summary
            row["has_pending"] = int(row.get("pending_turns") or 0) > 0
            if user_id and str(row.get("user_id", "")) != user_id:
                continue
            if realm and str(row.get("realm", "")) != realm:
                continue
            if pending == "yes" and not row["has_pending"]:
                continue
            if pending == "no" and row["has_pending"]:
                continue
            if query and query not in str(row.get("scope", "")).lower():
                continue
            rows.append(row)
        return {
            "rows": rows,
            "filters": {
                "user_id": user_id,
                "realm": realm,
                "pending": pending,
                "q": query,
            },
        }
