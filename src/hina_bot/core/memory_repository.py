"""Structured-memory persistence using the runtime Store transaction boundary."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from .memory_annotations import (
    Annotator,
    Audience,
    DirectiveType,
    MemoryAnnotation,
    Stability,
    TimeScope,
    UsageMode,
    admin_annotation,
    admin_annotation_fields,
    enforce_consistency,
)
from .memory_items import (
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    RelationshipEvidence,
    validate_memory_item_fields,
)
from .routing import Scope

_ANNOTATION_COLUMNS = (
    "memory_item_id", "item_revision", "time_scope", "stability", "observed_at", "event_at",
    "recurrence", "usage_mode", "directive_type", "audience", "importance", "slot", "keywords",
    "evidence", "explicit", "verified", "lint_flags", "cluster_id", "last_confirmed_at",
    "confirmations", "last_used_at", "proactive_status", "proactive_count", "last_proactive_at",
    "annotator", "annotator_version",
)


class MemoryRepository:
    """Participate in an extraction batch without committing its inner writes.

    The supplied write context preserves standalone commits and atomic batch writes.
    Resolving a speaker name is a conversation query, supplied by the Store facade.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        *,
        user_name: Callable[[Scope], str],
        write_context: Callable[[], AbstractContextManager],
    ):
        self.db = db
        self._user_name = user_name
        self._write_context = write_context

    @staticmethod
    def _decode_memory_item(row) -> MemoryItem:
        source_ids = json.loads(row["source_message_ids"] or "[]")
        relationship_evidence = RelationshipEvidence.from_mapping(
            json.loads(row["relationship_evidence"] or "{}")
        )
        return MemoryItem(
            id=int(row["id"]),
            user_id=str(row["user_id"]),
            user_name=str(row["user_name"] or ""),
            content=str(row["content"]),
            kind=MemoryKind(row["kind"]),
            origin_realm=str(row["origin_realm"]),
            origin_channel_id=str(row["origin_channel_id"]),
            origin_public_at_capture=bool(row["origin_public_at_capture"]),
            disclosure=MemoryDisclosure(row["disclosure"]),
            source_message_ids=tuple(str(value) for value in source_ids),
            confidence=float(row["confidence"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            revision=int(row["revision"]),
            relationship_evidence=relationship_evidence,
            status=MemoryStatus(row["status"]),
            superseded_by=(
                int(row["superseded_by"])
                if row["superseded_by"] is not None
                else None
            ),
        )

    @staticmethod
    def _decode_annotation(row) -> MemoryAnnotation:
        return MemoryAnnotation(
            memory_item_id=int(row["memory_item_id"]),
            item_revision=int(row["item_revision"]),
            time_scope=TimeScope(row["time_scope"]),
            stability=Stability(row["stability"]) if row["stability"] else None,
            observed_at=str(row["observed_at"]),
            event_at=row["event_at"],
            recurrence=row["recurrence"],
            usage_mode=UsageMode(row["usage_mode"]),
            directive_type=DirectiveType(row["directive_type"]) if row["directive_type"] else None,
            audience=Audience(row["audience"]),
            importance=int(row["importance"]),
            slot=str(row["slot"]),
            keywords=tuple(json.loads(row["keywords"] or "[]")),
            evidence=str(row["evidence"]),
            explicit=bool(row["explicit"]),
            verified=bool(row["verified"]),
            lint_flags=tuple(json.loads(row["lint_flags"] or "[]")),
            cluster_id=int(row["cluster_id"]) if row["cluster_id"] is not None else None,
            last_confirmed_at=str(row["last_confirmed_at"]),
            confirmations=int(row["confirmations"]),
            last_used_at=row["last_used_at"],
            proactive_status=str(row["proactive_status"]),
            proactive_count=int(row["proactive_count"]),
            last_proactive_at=row["last_proactive_at"],
            annotator=Annotator(row["annotator"]),
            annotator_version=str(row["annotator_version"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _encode_annotation(annotation: MemoryAnnotation) -> tuple:
        def enum(value):
            return value.value if value is not None else None

        return (
            int(annotation.memory_item_id), int(annotation.item_revision),
            annotation.time_scope.value, enum(annotation.stability), annotation.observed_at,
            annotation.event_at, annotation.recurrence, annotation.usage_mode.value,
            enum(annotation.directive_type), annotation.audience.value, int(annotation.importance),
            annotation.slot,
            json.dumps(list(annotation.keywords), ensure_ascii=False, separators=(",", ":")),
            annotation.evidence, int(annotation.explicit), int(annotation.verified),
            json.dumps(list(annotation.lint_flags), ensure_ascii=False, separators=(",", ":")),
            annotation.cluster_id, annotation.last_confirmed_at, int(annotation.confirmations),
            annotation.last_used_at, annotation.proactive_status, int(annotation.proactive_count),
            annotation.last_proactive_at, annotation.annotator.value, annotation.annotator_version,
        )

    def memory_annotation(self, item_id: int) -> MemoryAnnotation | None:
        row = self.db.execute(
            "SELECT * FROM memory_item_annotations WHERE memory_item_id=?", (int(item_id),)
        ).fetchone()
        return self._decode_annotation(row) if row is not None else None

    def memory_annotations(self, item_ids) -> dict[int, MemoryAnnotation]:
        ids = sorted({int(value) for value in item_ids})
        found: dict[int, MemoryAnnotation] = {}
        # Bounded chunks keep the statement under SQLite's variable limit.
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = self.db.execute(
                "SELECT * FROM memory_item_annotations WHERE memory_item_id IN "
                f"({','.join('?' for _ in chunk)})",
                chunk,
            ).fetchall()
            found.update({int(row["memory_item_id"]): self._decode_annotation(row) for row in rows})
        return found

    def upsert_memory_annotation(self, annotation: MemoryAnnotation) -> None:
        """Write one annotation; admin judgments are never overwritten by automation."""

        if int(annotation.memory_item_id) <= 0:
            raise ValueError("Annotation needs a stored memory item id")
        with self._write_context():
            item = self.db.execute(
                "SELECT kind FROM memory_items WHERE id=?", (int(annotation.memory_item_id),)
            ).fetchone()
            if item is None:
                raise ValueError("Memory item not found")
            if str(item["kind"]) == MemoryKind.RELATIONSHIP.value:
                raise ValueError("Relationship memories are not annotated")
            if annotation.annotator != Annotator.ADMIN:
                current = self.db.execute(
                    "SELECT annotator FROM memory_item_annotations WHERE memory_item_id=?",
                    (int(annotation.memory_item_id),),
                ).fetchone()
                if current is not None and current["annotator"] == Annotator.ADMIN.value:
                    return
            self._write_annotation_row(annotation)

    def _write_annotation_row(self, annotation: MemoryAnnotation) -> None:
        columns = ",".join(_ANNOTATION_COLUMNS)
        updates = ",".join(f"{name}=excluded.{name}" for name in _ANNOTATION_COLUMNS[1:])
        self.db.execute(
            f"INSERT INTO memory_item_annotations({columns}) VALUES "
            f"({','.join('?' for _ in _ANNOTATION_COLUMNS)}) "
            f"ON CONFLICT(memory_item_id) DO UPDATE SET {updates},"
            "updated_at=CURRENT_TIMESTAMP",
            self._encode_annotation(annotation),
        )

    def set_admin_memory_annotation(
        self, item_id: int, *, expected_revision: int, fields: dict[str, object]
    ) -> MemoryAnnotation:
        """Pin an admin judgment to the item revision the admin was looking at."""

        item_id = int(item_id)
        fields = admin_annotation_fields(fields)
        with self._write_context():
            item = self.db.execute(
                """SELECT kind,status,revision,created_at,origin_realm,origin_public_at_capture
                   FROM memory_items WHERE id=?""",
                (item_id,),
            ).fetchone()
            if item is None:
                raise ValueError("Memory item not found")
            if str(item["kind"]) == MemoryKind.RELATIONSHIP.value:
                raise ValueError("Relationship memories are not annotated")
            if str(item["status"]) != MemoryStatus.ACTIVE.value:
                raise ValueError("only active memory items can be annotated")
            if int(item["revision"]) != int(expected_revision):
                raise ValueError("Memory item has changed; reload before editing")
            current = self.db.execute(
                "SELECT * FROM memory_item_annotations WHERE memory_item_id=?", (item_id,)
            ).fetchone()
            annotation = enforce_consistency(
                admin_annotation(
                    fields,
                    memory_item_id=item_id,
                    item_revision=int(item["revision"]),
                    observed_at=str(item["created_at"]),
                    existing=self._decode_annotation(current) if current is not None else None,
                ),
                public_origin=str(item["origin_realm"]).startswith("guild:")
                and bool(item["origin_public_at_capture"]),
            )
            self._write_annotation_row(annotation)
        return annotation

    def record_memory_usage(self, usages) -> int:
        """Store reply usage metadata; never touches the annotation's judgments.

        Items without an annotation row are skipped: inventing a row would mark the
        item as annotated. Returns the number of rows updated.
        """

        updated = 0
        with self._write_context():
            for usage in usages:
                assignments, params = [], []
                if usage.last_used_at is not None:
                    assignments.append("last_used_at=MAX(COALESCE(last_used_at,''),?)")
                    params.append(usage.last_used_at)
                if usage.proactive_status is not None:
                    assignments += ["proactive_status=?", "proactive_count=?", "last_proactive_at=?"]
                    params += [usage.proactive_status, int(usage.proactive_count or 0),
                               usage.last_proactive_at]
                if not assignments:
                    continue
                cursor = self.db.execute(
                    f"UPDATE memory_item_annotations SET {','.join(assignments)} "
                    "WHERE memory_item_id=?",
                    (*params, int(usage.item_id)),
                )
                updated += max(0, int(cursor.rowcount or 0))
        return updated

    def reconfirm_memory_annotation(self, item_id: int, observed_at: str) -> bool:
        """Count a same-space re-mention as confirmation that the memory still holds."""

        with self._write_context():
            cursor = self.db.execute(
                """UPDATE memory_item_annotations
                   SET confirmations=confirmations+1,
                       last_confirmed_at=MAX(last_confirmed_at, ?),
                       updated_at=CURRENT_TIMESTAMP
                   WHERE memory_item_id=?""",
                (str(observed_at), int(item_id)),
            )
        return bool(cursor.rowcount)

    def add_memory_item(
        self,
        scope: Scope,
        content: str,
        *,
        kind: MemoryKind | str,
        disclosure: MemoryDisclosure | str,
        source_message_ids=(),
        confidence: float = 1.0,
        relationship_evidence: RelationshipEvidence | dict | None = None,
        user_name: str | None = None,
    ) -> int:
        fields = validate_memory_item_fields(
            content,
            kind=kind,
            disclosure=disclosure,
            confidence=confidence,
            relationship_evidence=relationship_evidence,
        )
        text = fields.content
        kind = fields.kind
        disclosure = fields.disclosure
        confidence = fields.confidence
        evidence = fields.relationship_evidence
        source_ids = tuple(dict.fromkeys(str(value) for value in source_message_ids))
        encoded_sources = json.dumps(source_ids, ensure_ascii=False, separators=(",", ":"))
        encoded_evidence = json.dumps(
            evidence.as_dict(), ensure_ascii=False, separators=(",", ":")
        )
        owner_name = self._user_name(scope) if user_name is None else str(user_name)[:100]
        with self._write_context():
            cursor = self.db.execute(
                "INSERT INTO memory_items(user_id,user_name,content,kind,origin_realm,origin_channel_id,"
                "origin_public_at_capture,disclosure,source_message_ids,confidence,"
                "relationship_evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(scope.user_id),
                    owner_name,
                    text,
                    kind.value,
                    scope.realm,
                    str(scope.channel_id),
                    int(scope.public_at_capture),
                    disclosure.value,
                    encoded_sources,
                    confidence,
                    encoded_evidence,
                ),
            )
        return int(cursor.lastrowid)

    def memory_items(
        self,
        user_id: int | str,
        *,
        origin_realm: str | None = None,
        include_superseded: bool = False,
    ):
        params: list[str] = [str(user_id)]
        where = "user_id=?"
        if include_superseded:
            # Retry deduplication intentionally includes superseded history, but a manual
            # retraction must not suppress a future re-observation of the same fact.
            where += " AND status IN ('active','superseded')"
        else:
            where += " AND status='active'"
        if origin_realm is not None:
            where += " AND origin_realm=?"
            params.append(origin_realm)
        rows = self.db.execute(
            f"SELECT * FROM memory_items WHERE {where} ORDER BY id",
            tuple(params),
        ).fetchall()
        return [self._decode_memory_item(row) for row in rows]

    def memory_item(self, item_id: int) -> MemoryItem | None:
        row = self.db.execute(
            "SELECT * FROM memory_items WHERE id=?",
            (int(item_id),),
        ).fetchone()
        return self._decode_memory_item(row) if row is not None else None

    def memory_item_edit_history(self, item_id: int):
        return self.db.execute(
            """SELECT * FROM memory_item_edit_history
               WHERE memory_item_id=? ORDER BY revision DESC,id DESC""",
            (int(item_id),),
        ).fetchall()

    def revise_memory_item(
        self,
        item_id: int,
        *,
        expected_revision: int,
        content: str,
        kind: MemoryKind | str,
        disclosure: MemoryDisclosure | str,
        confidence: float,
        relationship_evidence: RelationshipEvidence | dict | None = None,
        admin_command_id: int | None = None,
    ) -> MemoryItem:
        """Edit one active item in place while preserving its provenance and row identity."""

        item_id = int(item_id)
        expected_revision = int(expected_revision)
        if item_id <= 0:
            raise ValueError("Memory item id must be positive")
        if expected_revision < 0:
            raise ValueError("Memory item revision must not be negative")
        fields = validate_memory_item_fields(
            content,
            kind=kind,
            disclosure=disclosure,
            confidence=confidence,
            relationship_evidence=relationship_evidence,
        )
        encoded_evidence = json.dumps(
            fields.relationship_evidence.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )

        with self.db:
            row = self.db.execute(
                "SELECT * FROM memory_items WHERE id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Memory item not found")
            if str(row["status"]) != MemoryStatus.ACTIVE.value:
                raise ValueError("Only active memory items can be edited")
            current_revision = int(row["revision"])
            if current_revision != expected_revision:
                raise ValueError("Memory item has changed; reload before editing")

            current_evidence = RelationshipEvidence.from_mapping(
                json.loads(row["relationship_evidence"] or "{}")
            )
            changed = (
                str(row["content"]) != fields.content
                or str(row["kind"]) != fields.kind.value
                or str(row["disclosure"]) != fields.disclosure.value
                or float(row["confidence"]) != fields.confidence
                or current_evidence != fields.relationship_evidence
            )
            if not changed:
                return self._decode_memory_item(row)

            self.db.execute(
                """INSERT INTO memory_item_edit_history(
                       memory_item_id,revision,content,kind,disclosure,confidence,
                       relationship_evidence,admin_command_id
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    current_revision,
                    str(row["content"]),
                    str(row["kind"]),
                    str(row["disclosure"]),
                    float(row["confidence"]),
                    str(row["relationship_evidence"] or "{}"),
                    int(admin_command_id) if admin_command_id is not None else None,
                ),
            )
            cursor = self.db.execute(
                """UPDATE memory_items
                   SET content=?,kind=?,disclosure=?,confidence=?,relationship_evidence=?,
                       revision=revision+1,updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status='active' AND revision=?""",
                (
                    fields.content,
                    fields.kind.value,
                    fields.disclosure.value,
                    fields.confidence,
                    encoded_evidence,
                    item_id,
                    current_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Memory item has changed; reload before editing")
            updated = self.db.execute(
                "SELECT * FROM memory_items WHERE id=?",
                (item_id,),
            ).fetchone()
            if updated is None:
                raise RuntimeError("Memory item disappeared during edit")
            return self._decode_memory_item(updated)

    def retract_memory_item(
        self,
        item_id: int,
        *,
        expected_revision: int,
    ) -> MemoryItem:
        """Retract one active item without deleting its provenance or edit history."""

        item_id = int(item_id)
        expected_revision = int(expected_revision)
        if item_id <= 0:
            raise ValueError("Memory item id must be positive")
        if expected_revision < 0:
            raise ValueError("Memory item revision must not be negative")

        with self.db:
            row = self.db.execute(
                "SELECT * FROM memory_items WHERE id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Memory item not found")
            if str(row["status"]) != MemoryStatus.ACTIVE.value:
                raise ValueError("Only active memory items can be retracted")
            if int(row["revision"]) != expected_revision:
                raise ValueError("Memory item has changed; reload before retracting")
            cursor = self.db.execute(
                """UPDATE memory_items
                   SET status='retracted',superseded_by=NULL,revision=revision+1,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status='active' AND revision=?""",
                (item_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("Memory item has changed; reload before retracting")
            updated = self.db.execute(
                "SELECT * FROM memory_items WHERE id=?",
                (item_id,),
            ).fetchone()
            if updated is None:
                raise RuntimeError("Memory item disappeared during retraction")
            return self._decode_memory_item(updated)

    def delete_memory_item(self, item_id: int, *, expected_revision: int) -> None:
        """Permanently delete one structured item and its item-owned records.

        Other memories are never reactivated: superseded rows pointing here keep
        their lifecycle status but lose the dangling superseded_by link.
        """
        item_id = int(item_id)
        expected_revision = int(expected_revision)
        if item_id <= 0 or expected_revision < 0:
            raise ValueError("Invalid memory item deletion target")

        with self.db:
            row = self.db.execute(
                "SELECT revision FROM memory_items WHERE id=?", (item_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Memory item not found")
            if int(row["revision"]) != expected_revision:
                raise ValueError("Memory item has changed; reload before deleting")

            for table in ("memory_item_edit_history", "memory_item_annotations"):
                self.db.execute(f"DELETE FROM {table} WHERE memory_item_id=?", (item_id,))
            self.db.execute(
                """DELETE FROM memory_reconciliation_proposals
                   WHERE new_memory_item_id=? OR target_memory_item_id=?""",
                (item_id, item_id),
            )
            self.db.execute(
                """UPDATE memory_items
                   SET superseded_by=NULL,revision=revision+1,updated_at=CURRENT_TIMESTAMP
                   WHERE superseded_by=?""",
                (item_id,),
            )
            deleted = self.db.execute(
                "DELETE FROM memory_items WHERE id=? AND revision=?",
                (item_id, expected_revision),
            )
            if deleted.rowcount != 1:
                raise ValueError("Memory item has changed; reload before deleting")

    def reference_gated_memory_candidates(self, scope: Scope, *, limit: int = 24):
        """Return recent active owner memories whose cross-space access is reference-gated."""

        if scope.guild_id is None:
            return []
        rows = self.db.execute(
            """SELECT * FROM memory_items
               WHERE user_id=? AND status='active'
                 AND disclosure='reference_gated'
                 AND kind<>'relationship'
                 AND NOT (
                     origin_realm=?
                     AND (origin_public_at_capture=1 OR origin_channel_id=?)
                 )
               ORDER BY id DESC LIMIT ?""",
            (
                str(scope.user_id),
                scope.realm,
                str(scope.channel_id),
                max(0, int(limit)),
            ),
        ).fetchall()
        return [self._decode_memory_item(row) for row in rows]

    def memory_reconciliation_candidates(self, scope: Scope, *, limit: int = 24):
        """Return a bounded mix of active relationship and non-relationship items.

        Both queries enforce ownership and disclosure space in SQL. Reserve half
        of the slots for each group, then backfill unused slots by recency.
        """

        limit = max(0, int(limit))
        if not limit:
            return []
        where = "user_id=? AND status='active'"
        params: list[str] = [str(scope.user_id)]
        if scope.guild_id is not None:
            where += (
                " AND origin_realm=?"
                " AND (origin_public_at_capture=1 OR origin_channel_id=?)"
            )
            params.extend((scope.realm, str(scope.channel_id)))

        def read(relationship: bool):
            condition = "kind='relationship'" if relationship else "kind!='relationship'"
            rows = self.db.execute(
                f"SELECT * FROM memory_items WHERE {where} AND {condition}"
                " ORDER BY id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return rows

        factual, relationships = read(False), read(True)
        half = limit // 2
        chosen = factual[:half] + relationships[:limit - half]
        remaining = factual[half:] + relationships[limit - half:]
        chosen.extend(
            sorted(remaining, key=lambda row: int(row["id"]), reverse=True)[
                :limit - len(chosen)
            ]
        )
        return [
            self._decode_memory_item(row)
            for row in sorted(chosen, key=lambda row: int(row["id"]))
        ]

    def add_memory_reconciliation_proposal(
        self,
        scope: Scope,
        *,
        new_memory_item_id: int,
        target_memory_item_id: int,
        relation: str,
        confidence: float,
        source_message_ids=(),
    ) -> int | None:
        if relation not in {"duplicate", "corrects", "conflicts"}:
            raise ValueError("Invalid memory reconciliation relation")
        confidence = float(confidence)
        if not 0 <= confidence <= 1:
            raise ValueError("Memory reconciliation confidence must be between 0 and 1")
        if int(new_memory_item_id) == int(target_memory_item_id):
            raise ValueError("A memory item cannot reconcile with itself")
        owned = self.db.execute(
            """SELECT id,origin_realm,origin_channel_id,origin_public_at_capture
               FROM memory_items
               WHERE id IN (?,?) AND user_id=?""",
            (
                int(new_memory_item_id),
                int(target_memory_item_id),
                str(scope.user_id),
            ),
        ).fetchall()
        by_id = {int(row["id"]): row for row in owned}
        if set(by_id) != {int(new_memory_item_id), int(target_memory_item_id)}:
            raise ValueError("Reconciliation items must belong to the current user")
        new_item = by_id[int(new_memory_item_id)]
        target_item = by_id[int(target_memory_item_id)]
        if (
            str(new_item["origin_realm"]) != scope.realm
            or str(new_item["origin_channel_id"]) != str(scope.channel_id)
        ):
            raise ValueError("New reconciliation item must belong to the current space")
        if scope.guild_id is not None and not (
            str(target_item["origin_realm"]) == scope.realm
            and (
                bool(target_item["origin_public_at_capture"])
                or str(target_item["origin_channel_id"]) == str(scope.channel_id)
            )
        ):
            raise ValueError("Server reconciliation target is outside the disclosure space")
        source_ids = tuple(dict.fromkeys(str(value) for value in source_message_ids))
        encoded_sources = json.dumps(source_ids, ensure_ascii=False, separators=(",", ":"))
        with self._write_context():
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO memory_reconciliation_proposals(
                       user_id,origin_realm,origin_channel_id,new_memory_item_id,
                       target_memory_item_id,relation,confidence,source_message_ids
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    str(scope.user_id),
                    scope.realm,
                    str(scope.channel_id),
                    int(new_memory_item_id),
                    int(target_memory_item_id),
                    relation,
                    confidence,
                    encoded_sources,
                ),
            )
        return int(cursor.lastrowid) if cursor.rowcount else None

    def memory_reconciliation_proposal_id(
        self,
        *,
        new_memory_item_id: int,
        target_memory_item_id: int,
        relation: str,
    ) -> int | None:
        row = self.db.execute(
            """SELECT id FROM memory_reconciliation_proposals
               WHERE new_memory_item_id=? AND target_memory_item_id=? AND relation=?""",
            (int(new_memory_item_id), int(target_memory_item_id), str(relation)),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def memory_reconciliation_proposals(self, user_id: int | str):
        return self.db.execute(
            """SELECT * FROM memory_reconciliation_proposals
               WHERE user_id=? ORDER BY id""",
            (str(user_id),),
        ).fetchall()

    def apply_memory_reconciliation_proposal(
        self,
        proposal_id: int,
        *,
        min_confidence: float = 0.95,
    ) -> str:
        """Apply one conservative lifecycle transition and return its outcome."""

        threshold = float(min_confidence)
        if not 0 <= threshold <= 1:
            raise ValueError("Reconciliation confidence threshold must be between 0 and 1")
        row = self.db.execute(
            """SELECT p.*,
                      n.kind AS new_kind,n.status AS new_status,
                      n.disclosure AS new_disclosure,
                      n.origin_public_at_capture AS new_public,
                      n.relationship_evidence AS new_evidence,
                      n.superseded_by AS new_superseded_by,
                      n.origin_realm AS new_origin_realm,
                      n.origin_channel_id AS new_origin_channel_id,
                      t.kind AS target_kind,t.status AS target_status,
                      t.disclosure AS target_disclosure,
                      t.relationship_evidence AS target_evidence,
                      t.superseded_by AS target_superseded_by,
                      t.origin_realm AS target_origin_realm,
                      t.origin_channel_id AS target_origin_channel_id,
                      t.origin_public_at_capture AS target_public
               FROM memory_reconciliation_proposals p
               JOIN memory_items n
                 ON n.id=p.new_memory_item_id AND n.user_id=p.user_id
               JOIN memory_items t
                 ON t.id=p.target_memory_item_id AND t.user_id=p.user_id
               WHERE p.id=?""",
            (int(proposal_id),),
        ).fetchone()
        if row is None:
            return "missing"
        relation = str(row["relation"])
        if relation == "conflicts":
            return "conflict_deferred"
        if float(row["confidence"]) < threshold:
            return "below_threshold"
        # Decision 15: fact/preference/boundary labels are compatibility values and
        # may disagree on the same memory. Only the relationship boundary is a hard split.
        relationship = MemoryKind.RELATIONSHIP.value
        if (str(row["new_kind"]) == relationship) != (str(row["target_kind"]) == relationship):
            return "kind_mismatch"
        # Relationship corrections and differing evidence are observations,
        # not interchangeable facts. Only exact-evidence duplicates from
        # the same origin/disclosure can be automatically superseded.
        if (
            str(row["new_kind"]) == MemoryKind.RELATIONSHIP.value
            and (
                relation != "duplicate"
                or str(row["new_origin_realm"]) != str(row["target_origin_realm"])
                or str(row["new_origin_channel_id"]) != str(row["target_origin_channel_id"])
                or bool(row["new_public"]) != bool(row["target_public"])
                or str(row["new_disclosure"]) != str(row["target_disclosure"])
                or json.loads(row["new_evidence"] or "{}") != json.loads(row["target_evidence"] or "{}")
            )
        ):
            return "relationship_deferred"

        if (
            str(row["new_origin_realm"]) != str(row["origin_realm"])
            or str(row["new_origin_channel_id"]) != str(row["origin_channel_id"])
        ):
            return "invalid_origin"
        if str(row["origin_realm"]).startswith("guild:") and not (
            str(row["target_origin_realm"]) == str(row["origin_realm"])
            and (
                bool(row["target_public"])
                or str(row["target_origin_channel_id"]) == str(row["origin_channel_id"])
            )
        ):
            return "invalid_target_space"

        new_id = int(row["new_memory_item_id"])
        target_id = int(row["target_memory_item_id"])
        new_status = str(row["new_status"])
        target_status = str(row["target_status"])
        new_superseded_by = row["new_superseded_by"]
        target_superseded_by = row["target_superseded_by"]

        if relation == "duplicate":
            if (
                new_status == MemoryStatus.SUPERSEDED.value
                and new_superseded_by is not None
                and int(new_superseded_by) == target_id
            ):
                return "already_applied"
            if new_status != MemoryStatus.ACTIVE.value or target_status != MemoryStatus.ACTIVE.value:
                return "stale_items"
            with self._write_context():
                cursor = self.db.execute(
                    """UPDATE memory_items
                       SET status='superseded',superseded_by=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND user_id=? AND status='active'""",
                    (target_id, new_id, str(row["user_id"])),
                )
            return "applied" if cursor.rowcount else "stale_items"

        if relation == "corrects":
            if (
                target_status == MemoryStatus.SUPERSEDED.value
                and target_superseded_by is not None
                and int(target_superseded_by) == new_id
            ):
                return "already_applied"
            if new_status != MemoryStatus.ACTIVE.value or target_status != MemoryStatus.ACTIVE.value:
                return "stale_items"
            with self._write_context():
                cursor = self.db.execute(
                    """UPDATE memory_items
                       SET status='superseded',superseded_by=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND user_id=? AND status='active'""",
                    (new_id, target_id, str(row["user_id"])),
                )
            return "applied" if cursor.rowcount else "stale_items"

        return "unsupported_relation"

