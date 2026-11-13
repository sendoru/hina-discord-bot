"""P4 server memory: verified, source-owned storage. No LLM or response injection here.

The only write ingress is a completed bot interaction saved in `turns`.
Everything the model suggests is treated as untrusted until the stored source,
consent and disclosure checks succeed inside the same SQLite transaction.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .routing import Scope


@dataclass(frozen=True)
class ServerSourceRef:
    message_id: str
    role: str  # user | hina


def _ids(values: Iterable[int | str], *, maximum: int = 32) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(x) for x in values))
    if len(normalized) > maximum or any(
        not x.isascii() or not x.isdecimal() or int(x) <= 0 for x in normalized
    ):
        raise ValueError("Invalid or excessive Discord user IDs")
    return normalized


def _source_text(row, role: str) -> str:
    if role == "user":
        return str(row["content"])
    if role != "hina":
        raise ValueError("Invalid source role")
    try:
        provenance = json.loads(row["context_provenance"] or "{}")
    except (ValueError, TypeError):
        provenance = {}
    if not isinstance(provenance, dict):
        provenance = {}
    # The bot must never restate recalled material and then canonize it as a new memory.
    if provenance.get("history_lookup") or provenance.get("memory_lookup"):
        raise ValueError("A history/memory lookup reply cannot ground a server memory")
    return str(row["reply"])


class ServerMemoryRepository:
    def __init__(
        self,
        db,
        *,
        author_writable: Callable[[Scope], bool],
    ):
        self.db = db
        self.author_writable = author_writable

    def _source(self, guild_id: int, ref: ServerSourceRef):
        if ref.role not in ("user", "hina"):
            raise ValueError("Invalid source role")
        if not str(ref.message_id).isascii() or not str(ref.message_id).isdecimal():
            raise ValueError("Invalid source message ID")
        row = self.db.execute(
            "SELECT * FROM turns WHERE message_id=?", (str(ref.message_id),)
        ).fetchone()
        if row is None or row["realm"] != f"guild:{guild_id}":
            raise ValueError("Source turn not found in the requested server")
        parts = str(row["scope"]).split(":")
        if (
            len(parts) != 6
            or parts[:3] != ["guild", str(guild_id), "channel"]
            or parts[4:] != ["user", str(row["user_id"])]
        ):
            raise ValueError("Malformed source scope")
        channel_id = int(parts[3])
        user_id = int(row["user_id"])
        scope = Scope(guild_id, channel_id, user_id, bool(row["exportable"]))
        if self.opted_out(guild_id, user_id) or not self.author_writable(scope):
            raise PermissionError("Source author has disabled shared-memory contributions")
        try:
            provenance = json.loads(row["context_provenance"] or "{}")
        except (TypeError, ValueError):
            provenance = {}
        if not isinstance(provenance, dict) or provenance.get("source_trigger_kind") != "explicit":
            raise ValueError("Only explicitly addressed bot interactions can ground P4")
        text = _source_text(row, ref.role)
        return row, channel_id, scope, text

    def opted_out(self, guild_id: int, user_id: int | str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM server_memory_opt_outs WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ).fetchone() is not None

    def add_verified(
        self,
        guild_id: int,
        *,
        content: str,
        evidence: str,
        sources: Iterable[ServerSourceRef],
        subject_user_ids: Iterable[int | str] = (),
        time_scope: str = "unknown",
        stability: str | None = None,
        event_at: str | None = None,
        recurrence: str | None = None,
        usage_mode: str = "contextual",
        importance: int = 2,
        keywords: Iterable[str] = (),
    ) -> int:
        """Validate model-proposed memory against original completed, writable turns.

        For now there is no admin-approval writer, so a model cannot create a
        directive or assign itself admin trust.
        """
        guild_id = int(guild_id)
        if guild_id <= 0 or self.db.in_transaction:
            raise ValueError("Invalid guild or active transaction")
        content, evidence = str(content).strip(), str(evidence).strip()
        refs = tuple(sources)
        proposed_usage_mode = usage_mode
        if not content or len(content) > 600 or not evidence or len(evidence) > 120:
            raise ValueError("Content and a short cited evidence span are required")
        if not 1 <= len(refs) <= 32 or len(set(refs)) != len(refs):
            raise ValueError("Expected distinct original sources (1..32)")
        subjects = _ids(subject_user_ids)
        keywords = tuple(dict.fromkeys(str(k).strip() for k in keywords))
        if len(keywords) > 12 or any(not k or len(k) > 50 for k in keywords):
            raise ValueError("Invalid keywords")
        if usage_mode not in ("contextual", "ambient"):
            raise ValueError("Only verified admins may create server directives")
        if time_scope not in ("ongoing", "past", "planned", "episodic", "recurring", "unknown"):
            raise ValueError("Unknown time scope")
        if stability not in (None, "permanent", "long", "medium", "short"):
            raise ValueError("Unknown stability")
        if stability is not None and time_scope != "ongoing":
            raise ValueError("Stability applies only to ongoing memories")
        if not isinstance(importance, int) or not 1 <= importance <= 3:
            raise ValueError("Importance must be 1..3")

        self.db.execute("BEGIN IMMEDIATE")
        try:
            verified = [self._source(guild_id, ref) for ref in refs]
            public = bool(verified[0][2].public_at_capture)
            origin_channel_id = str(verified[0][1])
            if not all(
                bool(scope.public_at_capture) == public
                and (public or str(channel) == origin_channel_id)
                for _, channel, scope, _ in verified
            ):
                raise ValueError("Cannot combine public and private disclosure spaces")
            if not any(evidence in text for _, _, _, text in verified):
                raise ValueError("Evidence does not match its cited original turn")
            authors = {str(row["user_id"]) for row, _, _, _ in verified}
            if not set(subjects).issubset(authors):
                raise ValueError("Every named subject must have personally contributed")
            observed = any(
                ref.role == "hina" and evidence in text
                for ref, (_, _, _, text) in zip(refs, verified, strict=True)
            )
            space_key = "guild" if public else f"channel:{origin_channel_id}"
            now = max(str(row["created_at"]) for row, _, _, _ in verified)
            if observed and not subjects:
                trust = "observed"
            elif len(authors) > 1:
                trust = "corroborated"
            else:
                trust = "single"
            if trust == "single":
                usage_mode = "contextual"

            subjects_json = json.dumps(subjects, ensure_ascii=False, separators=(",", ":"))
            row = self.db.execute(
                """SELECT id,trust,usage_mode FROM server_memory_items
                   WHERE guild_id=? AND space_key=? AND content=?
                     AND subject_user_ids=? AND status='active'
                   ORDER BY id LIMIT 1""",
                (str(guild_id), space_key, content, subjects_json),
            ).fetchone()
            if row is None:
                cursor = self.db.execute(
                    """INSERT INTO server_memory_items(
                        guild_id,space_key,origin_channel_id,content,subject_user_ids,
                        trust,evidence,time_scope,stability,observed_at,event_at,recurrence,
                        usage_mode,importance,keywords,last_confirmed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(guild_id), space_key, origin_channel_id, content, subjects_json,
                        trust, evidence, time_scope, stability, now, event_at, recurrence,
                        usage_mode, importance, json.dumps(keywords, ensure_ascii=False),
                        now,
                    ),
                )
                item_id = int(cursor.lastrowid)
            else:
                item_id = int(row["id"])
            for ref, (source, channel, _, _) in zip(refs, verified, strict=True):
                self.db.execute(
                    """INSERT OR IGNORE INTO server_memory_sources(
                        item_id,message_id,source_channel_id,author_user_id,role,observed_at
                    ) VALUES (?,?,?,?,?,?)""",
                    (
                        item_id, str(ref.message_id), str(channel), str(source["user_id"]),
                        ref.role, str(source["created_at"]),
                    ),
                )
            if row is not None:
                all_sources = self.sources(item_id)
                author_count = len({x["author_user_id"] for x in all_sources})
                aggregate_trust = (
                    "observed" if row["trust"] == "observed" or trust == "observed"
                    else "corroborated" if author_count > 1 else "single"
                )
                aggregate_mode = (
                    "ambient" if aggregate_trust != "single"
                    and (row["usage_mode"] == "ambient" or proposed_usage_mode == "ambient")
                    else "contextual"
                )
                self.db.execute(
                    """UPDATE server_memory_items SET
                        trust=?,usage_mode=?,last_confirmed_at=?,confirmations=confirmations+1,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (aggregate_trust, aggregate_mode, now, item_id),
                )
            self.db.commit()
            return item_id
        except BaseException:
            self.db.rollback()
            raise

    def sources(self, item_id: int) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM server_memory_sources WHERE item_id=? ORDER BY message_id,role",
            (int(item_id),),
        )]

    def items(self, guild_id: int, *, limit: int = 100) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            """SELECT * FROM server_memory_items
               WHERE guild_id=? AND status='active' ORDER BY id DESC LIMIT ?""",
            (str(guild_id), max(0, min(1000, int(limit)))),
        )]

    def _delete_items(self, item_ids: Iterable[int]) -> int:
        ids = tuple(dict.fromkeys(int(i) for i in item_ids))
        if not ids:
            return 0
        binds = ",".join("?" for _ in ids)
        self.db.execute(f"DELETE FROM server_memory_sources WHERE item_id IN ({binds})", ids)
        return max(0, int(self.db.execute(
            f"DELETE FROM server_memory_items WHERE id IN ({binds})", ids
        ).rowcount))

    def forget_contributor(self, guild_id: int, user_id: int | str) -> int:
        """Borrow caller transaction; remove whole items instead of retaining quoted data."""
        rows = self.db.execute(
            """SELECT DISTINCT s.item_id FROM server_memory_sources s
               JOIN server_memory_items m ON m.id=s.item_id
               WHERE m.guild_id=? AND s.author_user_id=?""",
            (str(guild_id), str(user_id)),
        )
        return self._delete_items(row[0] for row in rows.fetchall())

    def delete_message(self, guild_id: int, channel_id: int, message_id: int) -> int:
        rows = self.db.execute(
            """SELECT DISTINCT s.item_id FROM server_memory_sources s
               JOIN server_memory_items m ON m.id=s.item_id
               WHERE m.guild_id=? AND s.source_channel_id=? AND s.message_id=?""",
            (str(guild_id), str(channel_id), str(message_id)),
        )
        return self._delete_items(row[0] for row in rows.fetchall())

    def purge_channel(self, guild_id: int, channel_id: int) -> int:
        rows = self.db.execute(
            """SELECT DISTINCT m.id FROM server_memory_items m
               LEFT JOIN server_memory_sources s ON s.item_id=m.id
               WHERE m.guild_id=? AND
                 (m.origin_channel_id=? OR s.source_channel_id=?)""",
            (str(guild_id), str(channel_id), str(channel_id)),
        )
        return self._delete_items(row[0] for row in rows.fetchall())

    def purge_guild(self, guild_id: int) -> int:
        rows = self.db.execute(
            "SELECT id FROM server_memory_items WHERE guild_id=?", (str(guild_id),)
        )
        return self._delete_items(row[0] for row in rows.fetchall())

    def purge_all(self) -> int:
        rows = self.db.execute("SELECT id FROM server_memory_items")
        return self._delete_items(row[0] for row in rows.fetchall())

    def set_opt_out(self, guild_id: int, user_id: int | str, enabled: bool) -> int:
        """Opt-out and withdrawal are one transaction; opt-in never revives old items."""
        if self.db.in_transaction:
            raise RuntimeError("Cannot change consent during an active transaction")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if enabled:
                self.db.execute(
                    """INSERT OR IGNORE INTO server_memory_opt_outs(guild_id,user_id)
                       VALUES (?,?)""", (str(guild_id), str(user_id)),
                )
                removed = self.forget_contributor(guild_id, user_id)
            else:
                self.db.execute(
                    "DELETE FROM server_memory_opt_outs WHERE guild_id=? AND user_id=?",
                    (str(guild_id), str(user_id)),
                )
                removed = 0
            self.db.commit()
            return removed
        except BaseException:
            self.db.rollback()
            raise

    def visible_items(
        self,
        scope: Scope,
        *,
        readable_channel_ids: Iterable[int | str],
        member_user_ids: Iterable[int | str],
        related_user_ids: Iterable[int | str] = (),
        limit: int = 100,
    ) -> list[dict]:
        """Fail closed unless the caller supplied *live* channel/member permissions.

        The runtime must fetch these from Discord each time, never from memory or
        stored channel metadata. This primitive must not be used as a permissions API.
        """
        if scope.guild_id is None:
            return []
        visible = set(_ids(readable_channel_ids, maximum=10000))
        members = set(_ids(member_user_ids, maximum=10000))
        related = set(_ids(related_user_ids))
        result = []
        for item in self.items(scope.guild_id, limit=limit):
            if item["space_key"] != "guild" and item["space_key"] != f"channel:{scope.channel_id}":
                continue
            sources = self.sources(item["id"])
            if not sources or any(
                s["source_channel_id"] not in visible
                or self.opted_out(scope.guild_id, s["author_user_id"])
                for s in sources
            ):
                continue
            authors = {s["author_user_id"] for s in sources}
            if authors.isdisjoint(members):
                continue  # All contributors have left; avoid stranded community claims.
            subjects = set(json.loads(item["subject_user_ids"]))
            if subjects and (
                not subjects.issubset(members)
                or str(scope.user_id) in subjects  # owner's personal memory handles this
                or subjects.isdisjoint(related)
            ):
                continue
            result.append(item)
        return result


__all__ = ["ServerMemoryRepository", "ServerSourceRef"]
