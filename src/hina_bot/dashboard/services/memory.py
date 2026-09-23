from __future__ import annotations

from hina_bot.core.memory_items import (
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    RelationshipEvidence,
)
from hina_bot.core.relationship_profile import (
    RELATIONSHIP_EVIDENCE_AXES,
    RELATIONSHIP_MAX_OBSERVATIONS,
    RELATIONSHIP_MIN_ITEM_CONFIDENCE,
    RELATIONSHIP_RECENCY_DECAY,
    aggregate_relationship_evidence,
    full_relationship_observations,
    implicit_relationship_observations,
)
from hina_bot.core.routing import Scope

from ..searchutils import search_matches
from ..timeutils import db_utc_timestamp
from .base import Page, ReadService, _decode_json


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

    @staticmethod
    def _typed_memory_item(row: dict[str, object]) -> MemoryItem:
        sources = _decode_json(row.get("source_message_ids"), [])
        evidence = _decode_json(row.get("relationship_evidence"), {})
        return MemoryItem(
            id=int(row["id"]),
            user_id=str(row["user_id"]),
            user_name=str(row.get("user_name") or ""),
            content=str(row.get("content") or ""),
            kind=MemoryKind(str(row["kind"])),
            origin_realm=str(row["origin_realm"]),
            origin_channel_id=str(row["origin_channel_id"]),
            origin_public_at_capture=bool(row.get("origin_public_at_capture")),
            disclosure=MemoryDisclosure(str(row["disclosure"])),
            source_message_ids=(
                tuple(str(item) for item in sources)
                if isinstance(sources, list)
                else ()
            ),
            confidence=float(row.get("confidence") or 0.0),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            relationship_evidence=RelationshipEvidence.from_mapping(
                evidence if isinstance(evidence, dict) else {}
            ),
            status=MemoryStatus(str(row.get("status") or "active")),
            superseded_by=(
                int(row["superseded_by"])
                if row.get("superseded_by") is not None
                else None
            ),
        )

    @staticmethod
    def _relationship_item_view(item: MemoryItem) -> dict[str, object]:
        return {
            "id": item.id,
            "content": item.content,
            "origin_realm": item.origin_realm,
            "origin_channel_id": item.origin_channel_id,
            "origin_public_at_capture": item.origin_public_at_capture,
            "disclosure": item.disclosure.value,
            "confidence": item.confidence,
            "evidence": item.relationship_evidence.as_dict(),
            "source_message_ids": item.source_message_ids,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def relationship_profiles(
        self,
        *,
        target_guild_id: str = "",
        target_channel_id: str = "",
        query: str = "",
    ) -> dict[str, object]:
        target_guild_id = target_guild_id.strip()
        target_channel_id = target_channel_id.strip()
        query = query.strip()

        target = None
        target_error = ""
        if target_guild_id or target_channel_id:
            try:
                guild_id = int(target_guild_id)
                channel_id = int(target_channel_id)
                if guild_id <= 0 or channel_id <= 0:
                    raise ValueError
                target = (guild_id, channel_id)
            except ValueError:
                target_error = "Target guild ID and channel ID must both be positive integers."

        rows = []
        for owner in self.repository.relationship_profile_users(query=query):
            user_id = str(owner["user_id"])
            raw_items = self.repository.relationship_profile_items(user_id)
            typed_items = []
            invalid_items = 0
            for raw in raw_items:
                try:
                    typed_items.append(self._typed_memory_item(raw))
                except (KeyError, TypeError, ValueError):
                    invalid_items += 1

            profile: dict[str, int] = {}
            contributors = []
            full_relationships = []
            if target is not None and user_id.isdigit():
                scope = Scope(target[0], target[1], int(user_id))
                full_relationships = [
                    self._relationship_item_view(item)
                    for item in full_relationship_observations(typed_items, scope)
                ]
                selected = implicit_relationship_observations(typed_items, scope)
                profile = aggregate_relationship_evidence(typed_items, scope)
                for age, item in enumerate(reversed(selected)):
                    contributor = self._relationship_item_view(item)
                    contributor["age"] = age
                    contributors.append(contributor)

            rows.append({
                **dict(owner),
                "profile": profile,
                "full_relationships": full_relationships,
                "full_relationship_count": len(full_relationships),
                "contributors": contributors,
                "used_observations": len(contributors),
                "invalid_observations": invalid_items,
            })

        return {
            "rows": rows,
            "filters": {
                "target_guild_id": target_guild_id,
                "target_channel_id": target_channel_id,
                "q": query,
            },
            "target": (
                {"guild_id": target[0], "channel_id": target[1]}
                if target is not None
                else None
            ),
            "target_error": target_error,
            "axes": RELATIONSHIP_EVIDENCE_AXES,
            "policy": {
                "min_confidence": RELATIONSHIP_MIN_ITEM_CONFIDENCE,
                "max_observations": RELATIONSHIP_MAX_OBSERVATIONS,
                "recency_decay": RELATIONSHIP_RECENCY_DECAY,
            },
        }

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
            "created_after": db_utc_timestamp(created_after, self.timezone),
            "created_before": db_utc_timestamp(created_before, self.timezone),
            "updated_after": db_utc_timestamp(updated_after, self.timezone),
            "updated_before": db_utc_timestamp(updated_before, self.timezone),
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
        view_rows = []
        for raw in rows:
            row = self._memory_row(raw)
            row["search_matches"] = search_matches(
                query,
                (
                    ("content", row.get("content")),
                    ("source message id", row.get("source_message_ids")),
                ),
            )
            view_rows.append(row)
        return {
            "rows": view_rows,
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
            "time_ranges": self.time_ranges(),
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
        names = self.repository.latest_user_names()
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
            value["user_name"] = str(
                row.get("name")
                or names.get(
                    (str(row.get("realm") or ""), str(row.get("user_id") or "")),
                    "",
                )
            )
            value["structured_memory_count"] = counts.get(str(row["user_id"]), 0)
            value["extraction_cursor"] = cursor_by_scope.get(str(row["scope"]))
            personal.append(value)

        shared = []
        for row in self.repository.shared_summary_status():
            if not keep(row):
                continue
            value = dict(row)
            value["user_name"] = str(
                row.get("name")
                or names.get(
                    (str(row.get("realm") or ""), str(row.get("user_id") or "")),
                    "",
                )
            )
            shared.append(value)
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
        names = self.repository.latest_user_names()
        rows: list[dict[str, object]] = []
        for raw in self.repository.extraction_cursor_status():
            row = dict(raw)
            row["user_name"] = names.get(
                (str(row.get("realm") or ""), str(row.get("user_id") or "")),
                "",
            )
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
            if query:
                haystack = " ".join(
                    (
                        str(row.get("scope", "")),
                        str(row.get("user_id", "")),
                        str(row.get("user_name", "")),
                    )
                ).lower()
                if query not in haystack:
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
