from __future__ import annotations

import json
from collections import Counter, defaultdict

from ..epochs import epoch_view, select_observability_epoch
from ..filterutils import validate_filters
from ..repository import AdminRepository
from ..scopepresenter import parse_scope_key, present_turn_scope
from ..searchutils import search_matches
from ..telemetry import TelemetryReader, TelemetrySnapshot
from ..timeutils import db_utc_timestamp, parse_local_time
from .base import Page, ReadService, _as_int, _parse_time, _timestamp, _turn_id

_TERMINAL_EVENTS = {"turn.completed", "turn.failed", "turn.dropped"}


def _group_trace_issues(candidates: list[dict[str, object]]) -> tuple[dict[str, object], ...]:
    grouped: dict[tuple[object, ...], dict[str, object]] = {}
    order: list[tuple[object, ...]] = []
    for candidate in candidates:
        fingerprint = str(candidate.get("fingerprint") or "")
        if fingerprint:
            key: tuple[object, ...] = ("fingerprint", fingerprint)
        else:
            key = (
                str(candidate.get("kind") or ""),
                str(candidate.get("source") or ""),
                str(candidate.get("operation") or ""),
                str(candidate.get("event") or ""),
                str(candidate.get("error_type") or ""),
                str(candidate.get("label") or ""),
            )
        count = max(1, _as_int(candidate.get("count")))
        if key in grouped:
            grouped[key]["count"] = _as_int(grouped[key].get("count")) + count
            continue
        row = dict(candidate)
        row["count"] = count
        grouped[key] = row
        order.append(key)
    return tuple(grouped[key] for key in order)


def _conversation_scope_fields(value: object) -> dict[str, str | None]:
    parts = str(value or "").split(":")
    fields = {
        parts[index]: parts[index + 1]
        for index in range(0, len(parts) - 1, 2)
    }
    scope_type = parts[0] if parts and parts[0] in {"guild", "dm"} else None
    return {
        "scope_type": scope_type,
        "guild_id": fields.get("guild"),
        "channel_id": fields.get("channel"),
    }


def _raw_turn_availability(
    stored: dict[str, object] | None,
    events: tuple[dict[str, object], ...],
    usage: tuple[dict[str, object], ...],
) -> dict[str, str]:
    if stored is not None:
        if stored.get("record_type") == "failed":
            return {
                "state": "retained",
                "severity": "info",
                "title": '실패한 대화 원본이 보존되어 있습니다',
                "message": (
                    '이 trace의 원본 질문과 fallback 응답이 관측용 기록에 보존되어 있습니다. '
                    '이 기록은 일반 대화 메모리에는 포함되지 않습니다.'
                ),
            }
        return {
            "state": "retained",
            "severity": "info",
            "title": '원본 대화가 보존되어 있습니다',
            "message": '이 trace의 원본 질문과 응답을 확인할 수 있습니다.',
        }

    terminal = next(
        (row for row in reversed(events) if row.get("event") in _TERMINAL_EVENTS),
        None,
    )
    if terminal is not None:
        persistence = str(terminal.get("raw_turn_persistence") or "")
        reason = str(terminal.get("raw_turn_persistence_reason") or "")
        if persistence == "failed":
            return {
                "state": "persistence_failed",
                "severity": "warning",
                "title": '원본 대화 저장에 실패했습니다',
                "message": (
                    'Discord 대화였지만 원본 질문과 응답을 저장하지 못했습니다.'
                ),
            }
        if persistence == "skipped":
            messages = {
                "memory_writes_disabled": (
                    '이 대화에서는 영구 메모리 쓰기가 비활성화되어 원본 질문과 응답을 저장하지 않았습니다.'
                ),
                "fixed_reply_no_raw_turn": (
                    '단순 호출에 대한 고정 응답이므로 원본 질문과 응답을 저장하지 않습니다.'
                ),
                "duplicate": (
                    '중복 대화로 판단되어 새 원본 질문과 응답을 저장하지 않았습니다.'
                ),
            }
            return {
                "state": "not_retained_by_design",
                "severity": "info",
                "title": '원본 대화를 저장하지 않았습니다',
                "message": messages.get(
                    reason,
                    '이 대화의 원본 저장을 의도적으로 생략했습니다.',
                ),
            }

    dropped = next(
        (row for row in reversed(events) if row.get("event") == "turn.dropped"),
        None,
    )
    if dropped is not None:
        reason = str(dropped.get("reason") or "unknown")
        return {
            "state": "not_retained_by_design",
            "severity": "info",
            "title": '원본 대화를 생성하지 않았습니다',
            "message": (
                "This Discord turn was dropped before raw conversation storage "
                f"(reason: {reason})."
            ),
        }

    received = next(
        (row for row in events if row.get("event") == "turn.received"),
        None,
    )
    operations = tuple(
        sorted(
            {
                str(row["operation"])
                for row in usage
                if isinstance(row.get("operation"), str) and row.get("operation")
            }
        )
    )
    answer_seen = "answer" in operations

    if received is not None:
        if (
            _as_int(received.get("content_chars")) == 0
            and not answer_seen
            and terminal is not None
            and terminal.get("event") == "turn.completed"
        ):
            return {
                "state": "not_retained_by_design",
                "severity": "info",
                "title": '원본 대화를 저장하지 않았습니다',
                "message": (
                    '단순 호출에 대한 고정 응답으로 보입니다. 이전 관측 데이터에는 저장 생략의 명시적 이유가 없습니다.'
                ),
            }
        return {
            "state": "unavailable",
            "severity": "warning",
            "title": '원본 대화를 확인할 수 없습니다',
            "message": (
                'Discord 대화 trace이지만 원본 질문과 응답이 보존되어 있지 않습니다. 이전 관측 데이터로는 보존 한계·분석 데이터 정리·메모리 쓰기 비활성화·저장 이유 표식 추가 이전 대화를 구분할 수 없습니다.'
            ),
        }

    if operations and not answer_seen:
        return {
            "state": "not_applicable",
            "severity": "info",
            "title": '원본 대화 저장 대상이 아닙니다',
            "message": (
                '이 trace는 Discord 대화 저장이 아닌 보조 모델 작업입니다. Operations: ' + ", ".join(operations) + "."
            ),
        }

    if answer_seen:
        return {
            "state": "unavailable",
            "severity": "warning",
            "title": '원본 대화를 확인할 수 없습니다',
            "message": (
                '응답 관측 데이터는 남아 있지만 대화 수명주기와 원본 질문·응답은 보존되어 있지 않습니다. 남은 관측 데이터만으로는 저장 여부의 이유를 확인할 수 없습니다.'
            ),
        }

    return {
        "state": "unknown",
        "severity": "warning",
        "title": '원본 대화의 보존 상태는 Unknown입니다',
        "message": (
            '원본 질문·응답이 보존되어 있지 않습니다. 남은 수명주기 정보로는 원본 저장 대상이었는지 판단할 수 없습니다.'
        ),
    }


class TraceService(ReadService):
    def __init__(
        self,
        repository: AdminRepository,
        telemetry: TelemetryReader,
        *,
        timezone: str = "Asia/Seoul",
    ):
        super().__init__(repository, timezone=timezone)
        self.telemetry = telemetry

    def overview(self) -> dict[str, object]:
        selection = select_observability_epoch(
            self.telemetry.snapshot(),
            self.repository.observability_epochs(),
        )
        snapshot = selection.snapshot
        traces = self._trace_summaries(snapshot)

        status_counts = Counter(str(row["status_label"]) for row in traces)
        scope_counts = Counter(str(row["scope"]) for row in traces if row["scope"])
        tier_counts = Counter(str(row["model_tier"]) for row in traces if row["model_tier"])

        exchange_rows = snapshot.exchanges
        exchange_ids = {_turn_id(row) for row in exchange_rows if _turn_id(row)}
        usage_ids = {
            _turn_id(row) for row in snapshot.usage
            if _turn_id(row) and row.get("operation") != "context.provenance"
        }
        missing_exchanges = len(usage_ids - exchange_ids)
        metrics = {}
        for field in (
            "calls", "input_tokens", "output_tokens", "total_tokens",
            "cached_tokens", "reasoning_tokens", "web_search_calls",
        ):
            known = [
                row[field] for row in exchange_rows
                if type(row.get(field)) is int and row[field] >= 0
            ]
            missing = len(exchange_rows) - len(known) + missing_exchanges
            metrics[field] = {
                "value": sum(known) if known else None,
                "known": len(known),
                "missing": missing,
                "partial": bool(known) and missing > 0,
            }
        calls = metrics["calls"]["value"]
        tokens = {field: metrics[field]["value"] for field in metrics if "tokens" in field}
        web_search_calls = metrics["web_search_calls"]["value"]

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
            "epoch": epoch_view(selection),
            "sources": self.telemetry.source_status(),
            "trace_count": len(traces),
            "stored_turn_count": self.repository.count_turns(),
            "status_counts": dict(status_counts),
            "scope_counts": dict(scope_counts),
            "tier_counts": dict(tier_counts),
            "metrics": metrics,
            "missing_exchanges": missing_exchanges,
            "api_calls": calls,
            "tokens": tokens,
            "web_search_calls": web_search_calls,
            "memory_failures": memory_failures,
            "memory_failure_traces": sum(row["memory_failures"] > 0 for row in traces),
            "failed_traces": sum(bool(row["error"]) for row in traces),
            "degraded_traces": sum(bool(row["degraded"]) for row in traces),
            "issue_traces": sum(bool(row["issue"]) for row in traces),
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
        issue: str = "",
        web_search: str = "",
        memory_failure: str = "",
        after: str = "",
        before: str = "",
        query: str = "",
        epoch: str = "",
    ) -> dict[str, object]:
        errors = validate_filters({"after": after, "before": before}, self.timezone)
        selection = select_observability_epoch(
            self.telemetry.snapshot(),
            self.repository.observability_epochs(),
            epoch,
        )
        snapshot = selection.snapshot
        rows = self._trace_summaries(snapshot)

        scope = scope.strip().lower()
        status = status.strip().lower()
        tier = tier.strip().lower()
        model = model.strip().lower()
        operation = operation.strip().lower()
        error = error.strip().lower()
        if error not in {"yes", "no"}:
            error = ""
        issue = issue.strip().lower()
        if issue not in {"yes", "no"}:
            issue = ""
        memory_failure = memory_failure.strip().lower()
        if memory_failure not in {"yes", "no"}:
            memory_failure = ""
        web_search = web_search.strip().lower()
        after = after.strip()
        before = before.strip()
        query = query.strip().lower()
        after_dt = parse_local_time(after, self.timezone)
        before_dt = parse_local_time(before, self.timezone)

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
            if issue == "yes" and not row.get("issue"):
                return False
            if issue == "no" and row.get("issue"):
                return False
            if memory_failure == "yes" and not row["memory_failures"]:
                return False
            if memory_failure == "no" and row["memory_failures"]:
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
                issue_terms = " ".join(
                    " ".join(
                        (
                            str(item.get("kind", "")),
                            str(item.get("source", "")),
                            str(item.get("operation", "")),
                            str(item.get("event", "")),
                            str(item.get("error_type", "")),
                            str(item.get("fingerprint", "")),
                            str(item.get("label", "")),
                        )
                    )
                    for item in row.get("issues", ())
                    if isinstance(item, dict)
                )
                haystack = " ".join(
                    (
                        str(row.get("turn_id", "")),
                        str(row.get("status", "")),
                        str(row.get("scope", "")),
                        str(row.get("model_tier", "")),
                        " ".join(models),
                        " ".join(operations),
                        str(row.get("error_fingerprint", "")),
                        issue_terms,
                    )
                ).lower()
                if query not in haystack:
                    return False
            return True

        rows = [row for row in rows if keep(row)] if not errors else []
        pagination = self._page(page, page_size, len(rows))
        if pagination.number > pagination.pages:
            pagination = Page(pagination.pages, pagination.size, pagination.total)
        start = (pagination.number - 1) * pagination.size
        end = start + pagination.size
        return {
            "filter_errors": errors,
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
                "issue": issue,
                "web_search": web_search,
                "memory_failure": memory_failure,
                "after": after,
                "before": before,
                "q": query,
                "epoch": selection.selected,
            },
            "epoch": epoch_view(selection),
            "time_ranges": self.time_ranges(),
        }

    def _context_provenance_view(
        self,
        raw,
        *,
        memory_context,
    ) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None

        metadata = self.repository.discord_scope_metadata()
        context_by_message: dict[str, dict] = {}
        if isinstance(memory_context, list):
            for item in memory_context:
                if not isinstance(item, dict):
                    continue
                message_id = str(item.get("message_id") or "")
                if message_id:
                    context_by_message[message_id] = item

        source_rows = []
        source_message_ids = []
        for source in raw.get("sources", ()) or ():
            if not isinstance(source, dict):
                continue
            row = dict(source)
            message_id = str(row.get("message_id") or "")
            if message_id:
                source_message_ids.append(message_id)
                causal = context_by_message.get(message_id)
                if causal is not None:
                    row["causal_context"] = causal
            source_rows.append(row)

        stored_turns = self.repository.turns_for_message_ids(source_message_ids)
        turn_by_message = {}
        for row in stored_turns:
            value = dict(row)
            self._decorate_scope_names(value, metadata)
            turn_by_message[str(value["message_id"])] = value
        for row in source_rows:
            message_id = str(row.get("message_id") or "")
            if message_id and message_id in turn_by_message:
                row["stored_turn"] = turn_by_message[message_id]

        structured_rows = []
        for item in raw.get("structured_memory", ()) or ():
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["origin_display"] = parse_scope_key(str(row.get("origin_realm") or ""))
            try:
                item_id = int(row.get("item_id"))
            except (TypeError, ValueError):
                item_id = 0
            if item_id:
                current = self.repository.memory_item(item_id)
                if current is not None:
                    row["current_status"] = current.get("status") or "active"
                    row["superseded_by"] = current.get("superseded_by")
                    row["user_name"] = current.get("user_name") or ""
            self._decorate_scope_names(
                row,
                metadata,
                realm_key="origin_realm",
                channel_key="origin_channel_id",
            )
            structured_rows.append(row)

        sections = [
            dict(row)
            for row in raw.get("sections", ()) or ()
            if isinstance(row, dict)
        ]
        return {
            "version": raw.get("version"),
            "scope": raw.get("scope"),
            "current_user_id": raw.get("current_user_id"),
            "egress_policy": raw.get("egress_policy"),
            "decisions": dict(raw.get("decisions") or {}),
            "egress": dict(raw.get("egress") or {}),
            "sections": sections,
            "sources": source_rows,
            "structured_memory": structured_rows,
            "relationship_axes": tuple(
                str(value) for value in raw.get("relationship_axes", ()) or ()
            ),
            "factual_recall": dict(raw.get("factual_recall") or {}),
            "truncated": dict(raw.get("truncated") or {}),
        }

    def trace(self, turn_id: str) -> dict[str, object] | None:
        trace_id = turn_id.strip()
        if not trace_id:
            return None
        telemetry = self.telemetry.for_turn(trace_id)
        stored = self.repository.turn_for_trace(trace_id)
        if stored is not None:
            stored = dict(stored)
            self._decorate_scope_names(stored, self.repository.discord_scope_metadata())
            stored = present_turn_scope(stored)
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

        context_provenance_raw: object = None
        if stored and stored.get("context_provenance"):
            raw = stored["context_provenance"]
            if isinstance(raw, str):
                try:
                    context_provenance_raw = json.loads(raw)
                except json.JSONDecodeError:
                    context_provenance_raw = None
        context_provenance = self._context_provenance_view(
            context_provenance_raw,
            memory_context=memory_context,
        )
        context_telemetry = next(
            (
                row
                for row in reversed(telemetry.usage)
                if row.get("operation") == "context.provenance"
            ),
            None,
        )

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
            "context_provenance": context_provenance,
            "context_telemetry": context_telemetry,
            "raw_turn_availability": _raw_turn_availability(
                stored,
                telemetry.events,
                telemetry.usage,
            ),
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
        after: str = "",
        before: str = "",
    ) -> dict[str, object]:
        errors = validate_filters({"after": after, "before": before}, self.timezone)
        page_size = min(100, max(10, int(page_size)))
        page = max(1, int(page))
        query = query.strip()
        repository_filters = {
            "scope": scope.strip(),
            "realm": realm.strip(),
            "user_id": user_id.strip(),
            "query": query,
            "created_after": db_utc_timestamp(after, self.timezone),
            "created_before": db_utc_timestamp(before, self.timezone),
        }
        total = 0 if errors else self.repository.count_conversations(**repository_filters)
        pagination = self._page(page, page_size, total)
        if pagination.number > pagination.pages:
            pagination = Page(pagination.pages, pagination.size, pagination.total)
        raw_rows = self.repository.search_conversations(
            **repository_filters,
            limit=pagination.size,
            offset=(pagination.number - 1) * pagination.size,
        ) if not errors else []
        metadata = self.repository.discord_scope_metadata()
        rows = []
        for raw in raw_rows:
            row = present_turn_scope(raw)
            row.update(_conversation_scope_fields(row.get("scope")))
            self._decorate_scope_names(row, metadata)
            row["search_matches"] = search_matches(
                query,
                (
                    ("input", row.get("content")),
                    ("reply", row.get("reply")),
                    ("message id", row.get("message_id")),
                    ("status", row.get("status")),
                    ("stage", row.get("stage")),
                    ("error", row.get("error_type")),
                    ("fingerprint", row.get("error_fingerprint")),
                ),
            )
            rows.append(row)
        return {
            "filter_errors": errors,
            "rows": rows,
            "page": pagination,
            "filters": {
                "scope": scope.strip(),
                "realm": realm.strip(),
                "user_id": user_id.strip(),
                "query": query,
                "after": after.strip(),
                "before": before.strip(),
            },
            "time_ranges": self.time_ranges(),
        }

    def conversation_context(
        self,
        turn_row_id: int,
        *,
        before: int = 5,
        after: int = 5,
    ) -> dict[str, object] | None:
        selected = self.repository.turn_by_id(turn_row_id)
        if selected is None:
            return None
        metadata = self.repository.discord_scope_metadata()
        rows = []
        for row in self.repository.turn_context(turn_row_id, before=before, after=after):
            value = present_turn_scope(row)
            value.update(_conversation_scope_fields(value.get("scope")))
            self._decorate_scope_names(value, metadata)
            value["selected"] = int(value["id"]) == int(turn_row_id)
            rows.append(value)
        return {
            "selected": selected,
            "rows": rows,
            "before": before,
            "after": after,
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
            preflight = next(
                (row for row in reversed(events) if row.get("event") == "turn.preflight"),
                None,
            )
            reply_delivered = next(
                (
                    row
                    for row in reversed(events)
                    if row.get("event") == "turn.reply_delivered"
                ),
                None,
            )

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
                known_tokens = [row["total_tokens"] for row in usage
                                if type(row.get("total_tokens")) is int]
                total_tokens = sum(known_tokens) if known_tokens else None

            # Keep the legacy field intact for compatibility, but derive explicit
            # latency views for the dashboard. Base turn elapsed_ms starts after the
            # web adapter preflight, while reply_delivered stops at the user-visible
            # Discord reply and terminal events may include post-reply memory work.
            if terminal and isinstance(terminal.get("elapsed_ms"), int):
                elapsed_ms = terminal["elapsed_ms"]
            elif exchange and isinstance(exchange.get("elapsed_ms"), int):
                elapsed_ms = exchange["elapsed_ms"]
            else:
                elapsed_ms = None

            preflight_ms = (
                preflight["preflight_ms"]
                if preflight and isinstance(preflight.get("preflight_ms"), int)
                else 0
            )
            reply_base_ms = (
                reply_delivered["elapsed_ms"]
                if reply_delivered
                and isinstance(reply_delivered.get("elapsed_ms"), int)
                else None
            )
            terminal_base_ms = (
                terminal["elapsed_ms"]
                if terminal and isinstance(terminal.get("elapsed_ms"), int)
                else None
            )
            reply_latency_ms = (
                preflight_ms + reply_base_ms
                if reply_base_ms is not None
                else None
            )
            turn_latency_ms = (
                preflight_ms + terminal_base_ms
                if terminal_base_ms is not None
                else None
            )
            post_reply_ms = (
                terminal_base_ms - reply_base_ms
                if (
                    terminal_base_ms is not None
                    and reply_base_ms is not None
                    and terminal_base_ms >= reply_base_ms
                )
                else None
            )
            exchange_elapsed_ms = (
                exchange["elapsed_ms"]
                if exchange and isinstance(exchange.get("elapsed_ms"), int)
                else None
            )

            if exchange:
                web_search_calls = _as_int(exchange.get("web_search_calls"))
            else:
                web_search_calls = sum(
                    _as_int(row.get("web_search_calls")) for row in usage
                )

            terminal_event = str(terminal.get("event") or "") if terminal else ""
            normalized_status = str(status_value or "incomplete").lower()
            final_failure = (
                terminal_event == "turn.failed"
                or (
                    terminal is None
                    and (
                        "fail" in normalized_status
                        or normalized_status == "error"
                    )
                )
            )

            memory_failures = sum(
                _as_int(event.get("memory_failures"))
                for event in events
                if event.get("event") == "turn.completed"
            )

            issue_candidates: list[dict[str, object]] = []
            usage_issue_count = 0
            detailed_fingerprints: set[str] = set()
            answer_error_seen = False

            for row in usage:
                if not (row.get("error_fingerprint") or row.get("status") == "error"):
                    continue
                operation = str(row.get("operation") or "")
                error_type = str(row.get("error_type") or "")
                fingerprint = str(row.get("error_fingerprint") or "")
                if fingerprint:
                    detailed_fingerprints.add(fingerprint)
                if operation == "answer":
                    answer_error_seen = True
                label = " · ".join(value for value in (operation, error_type) if value)
                issue_candidates.append({
                    "kind": "api_error",
                    "source": "usage",
                    "operation": operation,
                    "event": "",
                    "error_type": error_type,
                    "fingerprint": fingerprint,
                    "label": label or "API error",
                    "count": 1,
                })
                usage_issue_count += 1

            for row in events:
                if row is terminal or not (
                    row.get("error_fingerprint") or row.get("status") == "error"
                ):
                    continue
                event_name = str(row.get("event") or "")
                error_type = str(row.get("error_type") or "")
                fingerprint = str(row.get("error_fingerprint") or "")
                if fingerprint:
                    detailed_fingerprints.add(fingerprint)
                label = " · ".join(value for value in (event_name, error_type) if value)
                issue_candidates.append({
                    "kind": "event_error",
                    "source": "event",
                    "operation": "",
                    "event": event_name,
                    "error_type": error_type,
                    "fingerprint": fingerprint,
                    "label": label or "event error",
                    "count": 1,
                })

            terminal_error_row = (
                terminal
                if terminal and (
                    terminal.get("error_fingerprint")
                    or final_failure
                )
                else None
            )
            terminal_error_fingerprint = (
                str(terminal_error_row.get("error_fingerprint") or "")
                if terminal_error_row
                else ""
            )
            terminal_stage = (
                str(terminal_error_row.get("stage") or "")
                if terminal_error_row
                else ""
            )
            terminal_represented = bool(
                terminal_error_fingerprint
                and terminal_error_fingerprint in detailed_fingerprints
            ) or bool(
                final_failure
                and terminal_stage == "generation"
                and answer_error_seen
            )
            if final_failure and not terminal_represented:
                terminal_error_type = (
                    str(terminal_error_row.get("error_type") or "")
                    if terminal_error_row
                    else ""
                )
                terminal_label = " · ".join(
                    value
                    for value in (
                        terminal_stage or str(status_value or "turn failed"),
                        terminal_error_type,
                    )
                    if value
                )
                issue_candidates.append({
                    "kind": "turn_failure",
                    "source": "event" if terminal else "exchange",
                    "operation": "",
                    "event": terminal_event,
                    "error_type": terminal_error_type,
                    "fingerprint": terminal_error_fingerprint,
                    "label": terminal_label or "turn failed",
                    "count": 1,
                })

            if memory_failures > 0:
                issue_candidates.append({
                    "kind": "memory_failure",
                    "source": "event",
                    "operation": "",
                    "event": "turn.completed",
                    "error_type": "",
                    "fingerprint": "",
                    "label": "memory post-processing failure",
                    "count": memory_failures,
                })

            exchange_failed_calls = (
                _as_int(exchange.get("failed_calls")) if exchange else 0
            )
            missing_api_issue_count = max(
                0,
                exchange_failed_calls - usage_issue_count,
            )
            if missing_api_issue_count > 0:
                issue_candidates.append({
                    "kind": "api_error_aggregate",
                    "source": "exchange",
                    "operation": "",
                    "event": "",
                    "error_type": "",
                    "fingerprint": "",
                    "label": "API failure (aggregate)",
                    "count": missing_api_issue_count,
                })
            elif (
                exchange
                and exchange.get("status") == "completed_with_api_errors"
                and usage_issue_count == 0
                and exchange_failed_calls == 0
            ):
                # Older or partial telemetry may retain only the aggregate status.
                issue_candidates.append({
                    "kind": "api_error_aggregate",
                    "source": "exchange",
                    "operation": "",
                    "event": "",
                    "error_type": "",
                    "fingerprint": "",
                    "label": "API failure (aggregate)",
                    "count": 1,
                })

            issues = _group_trace_issues(issue_candidates)
            issue_count = sum(_as_int(item.get("count")) for item in issues)
            issue = issue_count > 0
            completed_like = (
                terminal_event == "turn.completed"
                or normalized_status in {"completed", "completed_with_api_errors"}
            )
            degraded = bool(completed_like and not final_failure and issue)

            status_label = (
                f"{status_value} · degraded"
                if degraded
                else str(status_value or "incomplete")
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
                    "status_label": status_label,
                    "degraded": degraded,
                    "models": tuple(sorted(models)),
                    "operations": operations,
                    "model_tier": model_tier,
                    "total_tokens": total_tokens,
                    "elapsed_ms": elapsed_ms,
                    "reply_latency_ms": reply_latency_ms,
                    "turn_latency_ms": turn_latency_ms,
                    "post_reply_ms": post_reply_ms,
                    "exchange_elapsed_ms": exchange_elapsed_ms,
                    "web_search": web_search_calls > 0,
                    "web_search_calls": web_search_calls,
                    "error": final_failure,
                    "error_fingerprint": (
                        str(terminal_error_row.get("error_fingerprint") or "")
                        if terminal_error_row
                        else ""
                    ),
                    "issue": issue,
                    "issues": issues,
                    "issue_count": issue_count,
                    "memory_failures": memory_failures,
                    "stored": trace_id in stored_by_trace,
                }
            )

        summaries.sort(key=lambda row: str(row["at"]), reverse=True)
        return summaries
