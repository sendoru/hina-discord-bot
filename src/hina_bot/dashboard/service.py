from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from .repository import AdminRepository
from .telemetry import TelemetryReader, TelemetrySnapshot

_TERMINAL_EVENTS = {"turn.completed", "turn.failed", "turn.dropped"}


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _timestamp(row: dict[str, object]) -> str:
    value = row.get("at")
    return value if isinstance(value, str) else ""


def _turn_id(row: dict[str, object]) -> str | None:
    value = row.get("turn_id")
    return value if isinstance(value, str) and value else None


def _decode_json(value: object, default):
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _db_timestamp(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = text.replace("T", " ")
    if len(text) == 16:
        text += ":00"
    return text


def _parse_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class Page:
    number: int
    size: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.size - 1) // self.size)

    @property
    def has_previous(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages


class DashboardService:
    def __init__(self, repository: AdminRepository, telemetry: TelemetryReader):
        self.repository = repository
        self.telemetry = telemetry

    @staticmethod
    def _page(number: int, size: int, total: int) -> Page:
        number = max(1, int(number))
        size = min(100, max(10, int(size)))
        return Page(number=number, size=size, total=max(0, int(total)))

    def overview(self) -> dict[str, object]:
        snapshot = self.telemetry.snapshot()
        traces = self._trace_summaries(snapshot)

        status_counts = Counter(str(row["status"]) for row in traces)
        scope_counts = Counter(str(row["scope"]) for row in traces if row["scope"])
        tier_counts = Counter(str(row["model_tier"]) for row in traces if row["model_tier"])

        exchange_rows = snapshot.exchanges
        calls = sum(_as_int(row.get("calls")) for row in exchange_rows)
        tokens = {
            field: sum(_as_int(row.get(field)) for row in exchange_rows)
            for field in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cached_tokens",
                "reasoning_tokens",
            )
        }
        web_search_calls = sum(_as_int(row.get("web_search_calls")) for row in exchange_rows)

        errors = Counter(
            str(row["error_fingerprint"])
            for row in snapshot.events
            if isinstance(row.get("error_fingerprint"), str)
        )
        memory_failures = sum(
            _as_int(row.get("memory_failures"))
            for row in snapshot.events
            if row.get("event") == "turn.completed"
        )

        return {
            "available": {
                "oldest_at": snapshot.oldest_at,
                "newest_at": snapshot.newest_at,
            },
            "sources": self.telemetry.source_status(),
            "trace_count": len(traces),
            "stored_turn_count": self.repository.count_turns(),
            "status_counts": dict(status_counts),
            "scope_counts": dict(scope_counts),
            "tier_counts": dict(tier_counts),
            "api_calls": calls,
            "tokens": tokens,
            "web_search_calls": web_search_calls,
            "memory_failures": memory_failures,
            "error_fingerprints": errors.most_common(8),
            "recent_traces": traces[:12],
        }

    def traces(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        scope: str = "",
        status: str = "",
        tier: str = "",
        model: str = "",
        operation: str = "",
        error: str = "",
        web_search: str = "",
        after: str = "",
        before: str = "",
        query: str = "",
    ) -> dict[str, object]:
        snapshot = self.telemetry.snapshot()
        rows = self._trace_summaries(snapshot)

        scope = scope.strip().lower()
        status = status.strip().lower()
        tier = tier.strip().lower()
        model = model.strip().lower()
        operation = operation.strip().lower()
        error = error.strip().lower()
        web_search = web_search.strip().lower()
        after = after.strip()
        before = before.strip()
        query = query.strip().lower()
        after_dt = _parse_time(after)
        before_dt = _parse_time(before)

        def keep(row: dict[str, object]) -> bool:
            if scope and str(row.get("scope", "")).lower() != scope:
                return False
            if status and str(row.get("status", "")).lower() != status:
                return False
            if tier and str(row.get("model_tier", "")).lower() != tier:
                return False
            models = tuple(str(value) for value in row.get("models", ()))
            if model and model not in " ".join(models).lower():
                return False
            operations = tuple(str(value) for value in row.get("operations", ()))
            if operation and operation not in " ".join(operations).lower():
                return False
            if error == "yes" and not row.get("error"):
                return False
            if error == "no" and row.get("error"):
                return False
            if web_search == "yes" and not row.get("web_search"):
                return False
            if web_search == "no" and row.get("web_search"):
                return False
            row_dt = _parse_time(str(row.get("at", "")))
            if after_dt and (row_dt is None or row_dt < after_dt):
                return False
            if before_dt and (row_dt is None or row_dt > before_dt):
                return False
            if query:
                haystack = " ".join(
                    (
                        str(row.get("turn_id", "")),
                        str(row.get("status", "")),
                        str(row.get("scope", "")),
                        str(row.get("model_tier", "")),
                        " ".join(models),
                        " ".join(operations),
                        str(row.get("error_fingerprint", "")),
                    )
                ).lower()
                if query not in haystack:
                    return False
            return True

        rows = [row for row in rows if keep(row)]
        pagination = self._page(page, page_size, len(rows))
        if pagination.number > pagination.pages:
            pagination = Page(pagination.pages, pagination.size, pagination.total)
        start = (pagination.number - 1) * pagination.size
        end = start + pagination.size
        return {
            "rows": rows[start:end],
            "page": pagination,
            "available": {
                "oldest_at": snapshot.oldest_at,
                "newest_at": snapshot.newest_at,
            },
            "filters": {
                "scope": scope,
                "status": status,
                "tier": tier,
                "model": model,
                "operation": operation,
                "error": error,
                "web_search": web_search,
                "after": after,
                "before": before,
                "q": query,
            },
        }

    def trace(self, turn_id: str) -> dict[str, object] | None:
        trace_id = turn_id.strip()
        if not trace_id:
            return None
        telemetry = self.telemetry.for_turn(trace_id)
        stored = self.repository.turn_for_trace(trace_id)
        if stored is None and not (telemetry.events or telemetry.usage or telemetry.exchanges):
            return None

        timeline: list[dict[str, object]] = []
        for source, rows in (
            ("event", telemetry.events),
            ("usage", telemetry.usage),
            ("exchange", telemetry.exchanges),
        ):
            for row in rows:
                timeline.append({"source": source, "at": _timestamp(row), "row": row})
        timeline.sort(key=lambda item: str(item["at"]))

        memory_context: object = None
        if stored and stored.get("memory_context"):
            raw = stored["memory_context"]
            if isinstance(raw, str):
                try:
                    memory_context = json.loads(raw)
                except json.JSONDecodeError:
                    memory_context = raw

        snapshot = TelemetrySnapshot(
            usage=telemetry.usage,
            exchanges=telemetry.exchanges,
            events=telemetry.events,
            oldest_at=None,
            newest_at=None,
        )
        summary = self._trace_summaries(snapshot, stored_turns=[stored] if stored else [])
        return {
            "turn_id": trace_id,
            "summary": summary[0] if summary else None,
            "stored": stored,
            "memory_context": memory_context,
            "timeline": timeline,
            "events": telemetry.events,
            "usage": telemetry.usage,
            "exchanges": telemetry.exchanges,
        }

    def conversations(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        query: str = "",
    ) -> dict[str, object]:
        page_size = min(100, max(10, int(page_size)))
        page = max(1, int(page))
        filters = {
            "scope": scope.strip(),
            "realm": realm.strip(),
            "user_id": user_id.strip(),
            "query": query.strip(),
        }
        total = self.repository.count_turns(**filters)
        pagination = self._page(page, page_size, total)
        if pagination.number > pagination.pages:
            pagination = Page(pagination.pages, pagination.size, pagination.total)
        rows = self.repository.search_turns(
            **filters,
            limit=pagination.size,
            offset=(pagination.number - 1) * pagination.size,
        )
        return {"rows": rows, "page": pagination, "filters": filters}

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

    def _trace_summaries(
        self,
        snapshot: TelemetrySnapshot,
        *,
        stored_turns: list[dict[str, object] | None] | None = None,
    ) -> list[dict[str, object]]:
        grouped: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(
            lambda: {"events": [], "usage": [], "exchanges": []}
        )
        for key, rows in (
            ("events", snapshot.events),
            ("usage", snapshot.usage),
            ("exchanges", snapshot.exchanges),
        ):
            for row in rows:
                trace_id = _turn_id(row)
                if trace_id:
                    grouped[trace_id][key].append(row)

        if stored_turns is None:
            stored_turns = self.repository.recent_turns(limit=1000)
        stored_by_trace = {
            str(row["turn_id"]): row
            for row in stored_turns
            if row and isinstance(row.get("turn_id"), str) and row.get("turn_id")
        }
        for trace_id in stored_by_trace:
            grouped.setdefault(trace_id, {"events": [], "usage": [], "exchanges": []})

        summaries: list[dict[str, object]] = []
        for trace_id, bucket in grouped.items():
            events = sorted(bucket["events"], key=_timestamp)
            usage = sorted(bucket["usage"], key=_timestamp)
            exchanges = sorted(bucket["exchanges"], key=_timestamp)

            terminal = next(
                (row for row in reversed(events) if row.get("event") in _TERMINAL_EVENTS),
                None,
            )
            received = next((row for row in events if row.get("event") == "turn.received"), None)
            exchange = exchanges[-1] if exchanges else None
            answer_rows = [row for row in usage if row.get("operation") == "answer"]

            timestamps = [
                value
                for row in (*events, *usage, *exchanges)
                if (value := _timestamp(row))
            ]
            if timestamps:
                at = min(timestamps)
            else:
                at = str(stored_by_trace.get(trace_id, {}).get("created_at", ""))

            if terminal:
                status_value = terminal.get("status") or terminal.get("event") or "incomplete"
            elif exchange:
                status_value = exchange.get("status") or "incomplete"
            else:
                status_value = "incomplete"

            if terminal and terminal.get("scope"):
                scope_value = terminal.get("scope")
            elif received and received.get("scope"):
                scope_value = received.get("scope")
            elif exchange:
                scope_value = exchange.get("scope") or ""
            else:
                scope_value = ""

            models: set[str] = set()
            operations = tuple(
                sorted(
                    {
                        str(row["operation"])
                        for row in usage
                        if isinstance(row.get("operation"), str) and row.get("operation")
                    }
                )
            )
            if exchange and isinstance(exchange.get("models"), list):
                models.update(str(value) for value in exchange["models"])
            models.update(
                str(row["model"])
                for row in usage
                if isinstance(row.get("model"), str) and row.get("model")
            )

            if exchange and isinstance(exchange.get("total_tokens"), int):
                total_tokens = exchange["total_tokens"]
            else:
                total_tokens = sum(_as_int(row.get("total_tokens")) for row in usage)

            if terminal and isinstance(terminal.get("elapsed_ms"), int):
                elapsed_ms = terminal["elapsed_ms"]
            elif exchange and isinstance(exchange.get("elapsed_ms"), int):
                elapsed_ms = exchange["elapsed_ms"]
            else:
                elapsed_ms = None

            if exchange:
                web_search_calls = _as_int(exchange.get("web_search_calls"))
            else:
                web_search_calls = sum(
                    _as_int(row.get("web_search_calls")) for row in usage
                )

            error_row = next(
                (
                    row
                    for row in reversed((*events, *usage))
                    if row.get("error_fingerprint") or row.get("status") == "error"
                ),
                None,
            )

            model_tier = ""
            for row in reversed(answer_rows):
                if isinstance(row.get("model_tier"), str):
                    model_tier = str(row["model_tier"])
                    break

            summaries.append(
                {
                    "turn_id": trace_id,
                    "at": at,
                    "scope": str(scope_value or ""),
                    "status": str(status_value or "incomplete"),
                    "models": tuple(sorted(models)),
                    "operations": operations,
                    "model_tier": model_tier,
                    "total_tokens": total_tokens,
                    "elapsed_ms": elapsed_ms,
                    "web_search": web_search_calls > 0,
                    "web_search_calls": web_search_calls,
                    "error": error_row is not None,
                    "error_fingerprint": (
                        str(error_row.get("error_fingerprint", "")) if error_row else ""
                    ),
                    "memory_failures": (
                        _as_int(terminal.get("memory_failures")) if terminal else 0
                    ),
                    "stored": trace_id in stored_by_trace,
                }
            )

        summaries.sort(key=lambda row: str(row["at"]), reverse=True)
        return summaries
