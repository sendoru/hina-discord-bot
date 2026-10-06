import json

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.analytics import build_analytics
from hina_bot.dashboard.epochs import select_observability_epoch
from hina_bot.dashboard.repository import AdminRepository
from hina_bot.dashboard.services import (
    ContextStateService,
    MemoryService,
    ReconciliationService,
    TraceService,
)
from hina_bot.dashboard.telemetry import TelemetryReader


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_service(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store(str(database))
    scope = Scope(1, 10, 100, True)
    store.observe_guild_channel(1, "Test Guild", 10, "general")
    memory_id = store.add_memory_item(
        scope,
        "trace relationship memory",
        kind="relationship",
        disclosure="implicit",
        source_message_ids=("44",),
        relationship_evidence={"familiarity": 2},
    )
    token = CURRENT_TURN_ID.set("trace-1")
    try:
        store.add(
            scope,
            55,
            "question",
            "answer",
            memory_context=[{
                "kind": "reply_reference_source",
                "message_id": "44",
                "role": "user",
                "ownership": "external",
                "author_user_id": "200",
                "content": "quoted source",
                "provenance_class": "reference_material",
            }],
            context_provenance={
                "version": 1,
                "scope": "guild",
                "current_user_id": "100",
                "egress_policy": "bot_interactions_only",
                "decisions": {
                    "use_memory": True,
                    "current_channel_only": False,
                    "cross_channel_memory": True,
                },
                "egress": {
                    "adapter": {
                        "channel_input": 3,
                        "channel_allowed": 2,
                        "channel_blocked": 1,
                        "public_input": 1,
                        "public_allowed": 1,
                        "public_blocked": 0,
                    },
                    "provider_boundary": {
                        "channel_input": 2,
                        "channel_allowed": 2,
                        "channel_blocked": 0,
                        "public_input": 1,
                        "public_allowed": 1,
                        "public_blocked": 0,
                    },
                },
                "sections": [
                    {
                        "name": "active_reply_chain",
                        "included": True,
                        "count": 1,
                        "blocked_count": 0,
                    },
                    {
                        "name": "structured_memory",
                        "included": True,
                        "count": 1,
                        "blocked_count": 0,
                    },
                ],
                "sources": [{
                    "source_type": "active_reply_chain",
                    "context_kind": "reply_reference_source",
                    "role": "user",
                    "owner_relation": "other",
                    "access": "full",
                    "is_reference": True,
                    "message_id": "44",
                    "author_user_id": "200",
                    "provenance_class": "reference_material",
                }],
                "structured_memory": [{
                    "item_id": memory_id,
                    "projection": "relationship_full",
                    "kind": "relationship",
                    "disclosure": "implicit",
                    "origin_realm": scope.realm,
                    "origin_channel_id": str(scope.channel_id),
                    "access": "full",
                }],
                "relationship_axes": [],
                "factual_recall": {
                    "detected": True,
                    "detector_reason": "past_reference",
                    "status": "no_candidates",
                    "candidate_count": 0,
                    "relevant_count": 0,
                    "selected_item_ids": [],
                    "authorization_reason": "",
                },
                "truncated": {"sources": 0, "structured_memory": 0},
            },
        )
    finally:
        CURRENT_TURN_ID.reset(token)
    store.close()

    usage = tmp_path / "logs" / "usage.jsonl"
    exchange = tmp_path / "logs" / "discord-usage.jsonl"
    events = tmp_path / "logs" / "events.jsonl"
    write_rows(
        usage,
        [
            {
                "at": "2026-09-21T00:00:01+00:00",
                "turn_id": "trace-1",
                "operation": "answer",
                "model": "smart-model",
                "model_tier": "smart",
                "total_tokens": 150,
                "web_search_calls": 1,
                "web_search_used": True,
                "elapsed_ms": 800,
                "status": "completed",
            },
            {
                "at": "2026-09-21T00:01:00.500000+00:00",
                "turn_id": "trace-2",
                "operation": "context.provenance",
                "status": "completed",
                "context_channel_items": 2,
                "context_reply_items": 1,
                "context_public_items": 0,
                "context_structured_items": 1,
                "context_lore_items": 0,
                "context_visual_items": 0,
                "context_adapter_blocked": 1,
                "context_provider_blocked": 0,
                "context_current_channel_only": False,
                "context_cross_channel_memory": True,
                "factual_recall_detected": True,
                "factual_recall_candidates": 2,
                "factual_recall_selected": 0,
                "factual_recall_status": "ambiguous_single_anchor",
            },
            {
                "at": "2026-09-21T00:01:01+00:00",
                "turn_id": "trace-2",
                "operation": "answer",
                "model": "fast-model",
                "model_tier": "fast",
                "total_tokens": 40,
                "web_search_calls": 0,
                "web_search_used": False,
                "status": "completed",
            },
        ],
    )
    write_rows(
        exchange,
        [
            {
                "at": "2026-09-21T00:00:01+00:00",
                "turn_id": "trace-1",
                "scope": "guild",
                "status": "completed",
                "models": ["smart-model"],
                "calls": 1,
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "cached_tokens": 20,
                "reasoning_tokens": 10,
                "web_search_calls": 1,
                "elapsed_ms": 900,
            },
            {
                "at": "2026-09-21T00:01:01+00:00",
                "turn_id": "trace-2",
                "scope": "dm",
                "status": "completed",
                "models": ["fast-model"],
                "calls": 1,
                "input_tokens": 30,
                "output_tokens": 10,
                "total_tokens": 40,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
                "web_search_calls": 0,
                "elapsed_ms": 400,
            },
        ],
    )
    write_rows(
        events,
        [
            {
                "at": "2026-09-21T00:00:00+00:00",
                "turn_id": "trace-1",
                "event": "turn.received",
                "scope": "guild",
            },
            {
                "at": "2026-09-21T00:00:02+00:00",
                "turn_id": "trace-1",
                "event": "turn.completed",
                "scope": "guild",
                "status": "completed",
                "elapsed_ms": 1000,
                "memory_failures": 1,
            },
            {
                "at": "2026-09-21T00:01:00+00:00",
                "turn_id": "trace-2",
                "event": "turn.received",
                "scope": "dm",
            },
            {
                "at": "2026-09-21T00:01:02+00:00",
                "turn_id": "trace-2",
                "event": "turn.failed",
                "scope": "dm",
                "status": "generation_failed",
                "elapsed_ms": 500,
                "error_fingerprint": "deadbeef",
                "raw_turn_persistence": "skipped",
                "raw_turn_persistence_reason": "memory_writes_disabled",
            },
        ],
    )
    return TraceService(
        AdminRepository(database),
        TelemetryReader(usage, events),
    )


def test_overview_aggregates_trace_and_exchange_metrics(tmp_path):
    service = build_service(tmp_path)

    data = service.overview()

    assert data["trace_count"] == 2
    assert data["stored_turn_count"] == 1
    assert data["status_counts"] == {
        "generation_failed": 1,
        "completed · degraded": 1,
    }
    assert data["tier_counts"] == {"fast": 1, "smart": 1}
    assert data["api_calls"] == 2
    assert data["tokens"]["total_tokens"] == 190
    assert data["web_search_calls"] == 1
    assert data["memory_failures"] == 1
    assert data["failed_traces"] == 1
    assert data["degraded_traces"] == 1
    assert data["issue_traces"] == 2
    assert data["error_fingerprints"] == [("deadbeef", 1)]


def test_trace_filters_and_pagination_are_server_side(tmp_path):
    service = build_service(tmp_path)

    data = service.traces(
        scope="guild",
        tier="smart",
        operation="answer",
        error="no",
        web_search="yes",
        after="2026-09-20T23:59:00+00:00",
        before="2026-09-21T00:00:30+00:00",
    )

    assert data["page"].total == 1
    assert [row["turn_id"] for row in data["rows"]] == ["trace-1"]
    assert data["rows"][0]["stored"] is True
    assert data["rows"][0]["models"] == ("smart-model",)
    assert data["rows"][0]["operations"] == ("answer",)
    assert data["rows"][0]["error"] is False
    assert data["rows"][0]["issue"] is True
    assert data["rows"][0]["degraded"] is True
    assert data["rows"][0]["issue_count"] == 1
    assert data["rows"][0]["issues"] == ({
        "kind": "memory_failure",
        "source": "event",
        "operation": "",
        "event": "turn.completed",
        "error_type": "",
        "fingerprint": "",
        "label": "memory post-processing failure",
        "count": 1,
    },)


def test_completed_trace_groups_multiple_classifier_errors_without_exchange_duplication(
    tmp_path,
):
    service = build_service(tmp_path)
    assert service.telemetry.usage_path is not None
    with service.telemetry.usage_path.open("a", encoding="utf-8") as handle:
        for at in (
            "2026-09-21T00:00:01.200000+00:00",
            "2026-09-21T00:00:01.300000+00:00",
        ):
            handle.write(json.dumps({
                "at": at,
                "turn_id": "trace-1",
                "operation": "model_route_classify",
                "status": "error",
                "error_type": "CancelledError",
                "elapsed_ms": 4001,
            }) + "\n")

    exchange_path = tmp_path / "logs" / "discord-usage.jsonl"
    exchange_rows = [
        json.loads(line)
        for line in exchange_path.read_text(encoding="utf-8").splitlines()
    ]
    for row in exchange_rows:
        if row["turn_id"] == "trace-1":
            row["status"] = "completed_with_api_errors"
            row["calls"] = 3
            row["failed_calls"] = 2
    write_rows(exchange_path, exchange_rows)

    detail = service.trace("trace-1")

    assert detail is not None
    summary = detail["summary"]
    assert summary["status"] == "completed"
    assert summary["error"] is False
    assert summary["issue"] is True
    assert summary["degraded"] is True
    assert summary["issue_count"] == 3
    assert len(summary["issues"]) == 2
    classifier_issue = next(
        issue for issue in summary["issues"]
        if issue["kind"] == "api_error"
    )
    assert classifier_issue["operation"] == "model_route_classify"
    assert classifier_issue["error_type"] == "CancelledError"
    assert classifier_issue["label"] == "model_route_classify · CancelledError"
    assert classifier_issue["count"] == 2
    assert not any(
        issue["kind"] == "api_error_aggregate"
        for issue in summary["issues"]
    )

    degraded = service.traces(error="no", issue="yes")
    assert "trace-1" in [row["turn_id"] for row in degraded["rows"]]
    assert service.traces(query="CancelledError")["page"].total == 1
    failed = service.traces(error="yes")
    assert [row["turn_id"] for row in failed["rows"]] == ["trace-2"]


def test_trace_detail_correlates_raw_turn_and_timeline(tmp_path):
    service = build_service(tmp_path)

    data = service.trace("trace-1")

    assert data is not None
    assert data["stored"]["content"] == "question"
    assert data["stored"]["guild_name"] == "Test Guild"
    assert data["stored"]["channel_name"] == "general"
    assert data["summary"]["status"] == "completed"
    assert data["context_provenance"]["egress_policy"] == "bot_interactions_only"
    assert data["context_provenance"]["egress"]["adapter"]["channel_blocked"] == 1
    assert data["context_provenance"]["factual_recall"]["detector_reason"] == "past_reference"
    assert data["context_provenance"]["factual_recall"]["status"] == "no_candidates"
    source = data["context_provenance"]["sources"][0]
    assert source["provenance_class"] == "reference_material"
    assert source["causal_context"]["content"] == "quoted source"
    memory = data["context_provenance"]["structured_memory"][0]
    assert memory["current_status"] == "active"
    assert memory["guild_name"] == "Test Guild"
    assert memory["channel_name"] == "general"
    assert [item["source"] for item in data["timeline"]] == [
        "event",
        "usage",
        "exchange",
        "event",
    ]
    assert service.trace("missing") is None


def test_trace_summary_separates_reply_and_total_latency(tmp_path):
    service = build_service(tmp_path)
    assert service.telemetry.event_path is not None
    with service.telemetry.event_path.open("a", encoding="utf-8") as handle:
        for row in (
            {
                "at": "2026-09-21T00:00:00.100000+00:00",
                "turn_id": "trace-1",
                "event": "turn.preflight",
                "scope": "guild",
                "preflight_ms": 100,
            },
            {
                "at": "2026-09-21T00:00:01.500000+00:00",
                "turn_id": "trace-1",
                "event": "turn.reply_delivered",
                "scope": "guild",
                "elapsed_ms": 700,
                "delivery_ms": 50,
            },
        ):
            handle.write(json.dumps(row) + "\n")

    data = service.trace("trace-1")

    assert data is not None
    summary = data["summary"]
    assert summary["elapsed_ms"] == 1000
    assert summary["reply_latency_ms"] == 800
    assert summary["turn_latency_ms"] == 1100
    assert summary["post_reply_ms"] == 300
    assert summary["exchange_elapsed_ms"] == 900


def test_failed_trace_keeps_content_free_context_telemetry(tmp_path):
    service = build_service(tmp_path)

    data = service.trace("trace-2")

    assert data is not None
    assert data["stored"] is None
    assert data["context_provenance"] is None
    assert data["context_telemetry"]["context_reply_items"] == 1
    assert data["context_telemetry"]["context_adapter_blocked"] == 1
    assert data["context_telemetry"]["factual_recall_detected"] is True
    assert data["context_telemetry"]["factual_recall_status"] == "ambiguous_single_anchor"
    assert data["raw_turn_availability"]["state"] == "not_retained_by_design"
    assert "비활성화" in data["raw_turn_availability"]["message"]


def test_auxiliary_trace_does_not_claim_raw_turn_expired(tmp_path):
    service = build_service(tmp_path)
    assert service.telemetry.usage_path is not None
    with service.telemetry.usage_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "at": "2026-09-21T00:02:00+00:00",
            "turn_id": "trace-summary",
            "operation": "summarize",
            "model": "fast-model",
            "status": "completed",
            "total_tokens": 25,
        }) + "\n")

    data = service.trace("trace-summary")

    assert data is not None
    assert data["stored"] is None
    assert data["raw_turn_availability"]["state"] == "not_applicable"
    assert "summarize" in data["raw_turn_availability"]["message"]


def test_conversations_use_bounded_repository_filters(tmp_path):
    service = build_service(tmp_path)

    data = service.conversations(query="question")

    assert data["page"].total == 1
    assert data["rows"][0]["reply"] == "answer"
    assert data["rows"][0]["scope_type"] == "guild"
    assert data["rows"][0]["guild_id"] == "1"
    assert data["rows"][0]["channel_id"] == "10"
    assert data["rows"][0]["guild_name"] == "Test Guild"
    assert data["rows"][0]["channel_name"] == "general"
    assert service.conversations(query="not-found")["page"].total == 0


def test_conversations_surface_failed_turn_status_and_trace(tmp_path):
    service = build_service(tmp_path)
    store = Store(str(service.repository.path))
    scope = Scope(1, 10, 100, True)
    token = CURRENT_TURN_ID.set("trace-failed-stored")
    try:
        store.add_failed_turn(
            scope,
            77,
            "failed input",
            name="Failed User",
            reply="fallback reply",
            status="generation_failed",
            stage="generation",
            reply_delivered=True,
            error_type="ValueError",
            error_fingerprint="cafebabe",
        )
    finally:
        CURRENT_TURN_ID.reset(token)
        store.close()

    data = service.conversations(query="failed input")

    assert data["page"].total == 1
    row = data["rows"][0]
    assert row["record_type"] == "failed"
    assert row["reply"] == "fallback reply"
    assert row["status"] == "generation_failed"
    assert row["stage"] == "generation"
    assert row["turn_id"] == "trace-failed-stored"
    assert row["scope_type"] == "guild"
    assert row["guild_id"] == "1"
    assert row["channel_id"] == "10"

    trace = service.trace("trace-failed-stored")
    assert trace is not None
    assert trace["stored"]["record_type"] == "failed"
    assert trace["stored"]["content"] == "failed input"
    assert trace["stored"]["reply"] == "fallback reply"
    assert trace["stored"]["status"] == "generation_failed"
    assert trace["memory_context"] is None
    assert trace["context_provenance"] is None
    assert trace["raw_turn_availability"]["state"] == "retained"
    assert "관측용" in trace["raw_turn_availability"]["message"]


def build_memory_service(tmp_path):
    database = tmp_path / "memory.sqlite3"
    store = Store(str(database))
    scope = Scope(1, 10, 100, True)
    store.observe_guild_channel(1, "Memory Guild", 10, "memory")
    token = CURRENT_TURN_ID.set("memory-trace")
    try:
        store.add(scope, 700, "source for memory", "reply", name="Memory User")
    finally:
        CURRENT_TURN_ID.reset(token)
    item_id = store.add_memory_item(
        scope,
        "remembered fact",
        kind="fact",
        disclosure="reference_gated",
        source_message_ids=("700",),
        confidence=0.91,
    )
    store.close()
    return (
        MemoryService(AdminRepository(database)),
        item_id,
        scope,
    )


def test_memory_service_filters_and_source_drilldown(tmp_path):
    service, item_id, _ = build_memory_service(tmp_path)

    listing = service.memory_items(
        user_id="100",
        kind="fact",
        disclosure="reference_gated",
        confidence_min="0.9",
        query="remembered",
    )
    assert listing["page"].total == 1
    assert listing["rows"][0]["source_message_ids_decoded"] == ("700",)
    assert listing["rows"][0]["guild_name"] == "Memory Guild"
    assert listing["rows"][0]["channel_name"] == "memory"

    detail = service.memory_item(item_id)
    assert detail is not None
    assert detail["item"]["content"] == "remembered fact"
    assert detail["item"]["guild_name"] == "Memory Guild"
    assert detail["item"]["channel_name"] == "memory"
    assert detail["sources"][0]["turn"]["turn_id"] == "memory-trace"
    assert detail["sources"][0]["turn"]["channel_id"] == "10"
    assert detail["sources"][0]["turn"]["channel_name"] == "memory"


def test_memory_scope_picker_filters_guild_and_dm_without_raw_realms(tmp_path):
    database = tmp_path / "scope-filter-memory.sqlite3"
    store = Store(str(database))
    guild_scope = Scope(1, 10, 100, True)
    other_guild_scope = Scope(2, 11, 100, True)
    dm_scope = Scope(None, 20, 100)
    other_dm_scope = Scope(None, 21, 100)

    store.add_memory_item(
        guild_scope,
        "guild one",
        kind="fact",
        disclosure="local",
    )
    store.add_memory_item(
        other_guild_scope,
        "guild two",
        kind="fact",
        disclosure="local",
    )
    store.add_memory_item(
        dm_scope,
        "dm one",
        kind="fact",
        disclosure="local",
    )
    store.add_memory_item(
        other_dm_scope,
        "dm two",
        kind="fact",
        disclosure="local",
    )
    store.close()

    service = MemoryService(AdminRepository(database))

    all_dms = service.memory_items(origin_scope_type="dm")
    assert {row["content"] for row in all_dms["rows"]} == {"dm one", "dm two"}
    assert all_dms["filters"]["origin_scope_type"] == "dm"
    assert all_dms["filters"]["origin_guild_id"] == ""

    exact_dm = service.memory_items(
        origin_scope_type="dm",
        origin_channel_id="20",
    )
    assert [row["content"] for row in exact_dm["rows"]] == ["dm one"]

    all_guilds = service.memory_items(origin_scope_type="guild")
    assert {row["content"] for row in all_guilds["rows"]} == {
        "guild one",
        "guild two",
    }

    exact_guild = service.memory_items(
        origin_scope_type="guild",
        origin_guild_id="1",
    )
    assert [row["content"] for row in exact_guild["rows"]] == ["guild one"]

    legacy = service.memory_items(
        origin_realm="dm:100",
        origin_channel_id="20",
    )
    assert [row["content"] for row in legacy["rows"]] == ["dm one"]
    assert legacy["filters"]["origin_scope_type"] == "dm"
    assert legacy["filters"]["origin_channel_id"] == "20"


def test_cursor_service_shows_structured_extraction_state(tmp_path):
    service, _, scope = build_memory_service(tmp_path)

    summaries = service.summaries(user_id="100")
    assert summaries["shared"] == []

    cursors = service.extraction_cursors(user_id="100")
    row = next(row for row in cursors["rows"] if row["scope"] == scope.conversation)
    assert row["user_name"] == "Memory User"
    assert row["scope_display"].raw == scope.conversation
    assert row["initialized"] == 0
    assert row["effective_through_id"] == 0
    assert row["pending_turns"] == 1

def build_reconciliation_service(tmp_path):
    database = tmp_path / "reconciliation.sqlite3"
    store = Store(str(database))
    scope = Scope(None, 10, 100)

    token = CURRENT_TURN_ID.set("trace-old")
    try:
        store.add(scope, 101, "old source", "reply", name="Reconciliation User")
    finally:
        CURRENT_TURN_ID.reset(token)
    token = CURRENT_TURN_ID.set("trace-extract")
    try:
        store.add(scope, 102, "new correction source", "reply", name="Reconciliation User")
    finally:
        CURRENT_TURN_ID.reset(token)

    target_id = store.add_memory_item(
        scope,
        "old remembered value",
        kind="fact",
        disclosure="reference_gated",
        source_message_ids=("101", "102"),
        confidence=0.8,
    )
    new_id = store.add_memory_item(
        scope,
        "new remembered value",
        kind="fact",
        disclosure="reference_gated",
        source_message_ids=("102", "101"),
        confidence=0.95,
    )
    proposal_id = store.add_memory_reconciliation_proposal(
        scope,
        new_memory_item_id=new_id,
        target_memory_item_id=target_id,
        relation="corrects",
        confidence=0.93,
        source_message_ids=("102", "101"),
    )
    store.close()

    usage = tmp_path / "logs" / "usage.jsonl"
    events = tmp_path / "logs" / "events.jsonl"
    write_rows(
        usage,
        [
            {
                "at": "2026-09-21T01:00:00+00:00",
                "turn_id": "trace-extract",
                "operation": "memory.shadow_extraction",
                "status": "requested",
                "memory_kind": "structured_shadow",
                "pending_turns": 4,
                "batch_turns": 4,
            },
            {
                "at": "2026-09-21T01:00:01+00:00",
                "turn_id": "trace-extract",
                "operation": "extract_memory_items_shadow",
                "model": "memory-model",
                "status": "completed",
                "total_tokens": 120,
            },
            {
                "at": "2026-09-21T01:00:02+00:00",
                "turn_id": "trace-extract",
                "operation": "memory.shadow_extraction",
                "status": "completed",
                "memory_kind": "structured_shadow",
                "pending_turns": 4,
                "batch_turns": 4,
            },
        ],
    )
    write_rows(
        events,
        [
            {
                "at": "2026-09-21T01:00:03+00:00",
                "turn_id": "trace-extract",
                "event": "turn.completed",
                "status": "completed",
                "scope": "dm",
            }
        ],
    )
    return (
        ReconciliationService(
            AdminRepository(database),
            TelemetryReader(usage, events),
        ),
        proposal_id,
    )


def test_reconciliation_service_list_stats_and_detail_context(tmp_path):
    service, proposal_id = build_reconciliation_service(tmp_path)

    listing = service.reconciliation_proposals(
        relation="corrects",
        kind="fact",
        retry="yes",
        confidence_min="0.9",
        query="remembered",
    )

    assert listing["page"].total == 1
    assert listing["stats"]["retry_suspects"] == 1
    assert listing["rows"][0]["user_name"] == "Reconciliation User"
    assert listing["rows"][0]["same_source_set"] is True
    assert listing["rows"][0]["source_overlap_ids"] == ("101", "102")

    dm_scope = service.reconciliation_proposals(origin_scope_type="dm")
    assert dm_scope["page"].total == 1
    assert dm_scope["filters"]["origin_scope_type"] == "dm"
    assert service.reconciliation_proposals(origin_scope_type="guild")["page"].total == 0

    detail = service.reconciliation_proposal(proposal_id)
    assert detail is not None
    assert detail["proposal"]["user_name"] == "Reconciliation User"
    assert detail["new_item"]["content"] == "new remembered value"
    assert detail["target_item"]["content"] == "old remembered value"
    assert {row["message_id"] for row in detail["sources"]} == {"101", "102"}
    trace = next(
        row for row in detail["extraction_traces"]
        if row["turn_id"] == "trace-extract"
    )
    assert [row["operation"] for row in trace["usage"]] == [
        "memory.shadow_extraction",
        "extract_memory_items_shadow",
        "memory.shadow_extraction",
    ]
    assert trace["events"][0]["event"] == "turn.completed"


def test_conversation_service_local_time_filter_and_search_match(tmp_path):
    service = build_service(tmp_path)
    service.timezone = "Asia/Seoul"

    data = service.conversations(
        query="question",
        after="2020-01-01T00:00",
        before="2030-01-01T00:00",
    )

    assert data["page"].total == 1
    assert data["rows"][0]["search_matches"][0]["field"] == "input"
    assert data["rows"][0]["search_matches"][0]["fragment"]["match"] == "question"
    assert data["time_ranges"]


def test_conversation_context_marks_selected_turn(tmp_path):
    service = build_service(tmp_path)
    row_id = int(service.repository.search_turns(limit=1)[0]["id"])

    data = service.conversation_context(row_id, before=2, after=2)

    assert data is not None
    selected = [row for row in data["rows"] if row["selected"]]
    assert len(selected) == 1
    assert selected[0]["id"] == row_id
    assert selected[0]["guild_id"] == "1"
    assert selected[0]["channel_id"] == "10"



def test_relationship_profiles_match_runtime_cross_space_projection(tmp_path):
    database = tmp_path / "relationships.sqlite3"
    store = Store(str(database))
    dm = Scope(None, 10, 100)
    same_target_guild = Scope(1, 20, 100, True)
    store.observe_guild_channel(1, "Profile Guild", 20, "profile")
    store.observe_guild_channel(1, "Profile Guild", 99, "target")

    first_id = store.add_memory_item(
        dm,
        "first cross-space relationship observation",
        kind="relationship",
        disclosure="implicit",
        confidence=0.9,
        relationship_evidence={"familiarity": 2},
        user_name="Profile User",
    )
    second_id = store.add_memory_item(
        dm,
        "second cross-space relationship observation",
        kind="relationship",
        disclosure="implicit",
        confidence=0.9,
        relationship_evidence={"familiarity": 2},
        user_name="Profile User",
    )
    same_space_id = store.add_memory_item(
        same_target_guild,
        "same disclosure space relationship",
        kind="relationship",
        disclosure="implicit",
        confidence=0.99,
        relationship_evidence={"casualness": 4},
        user_name="Profile User",
    )
    low_conf_id = store.add_memory_item(
        dm,
        "low confidence relationship",
        kind="relationship",
        disclosure="implicit",
        confidence=0.79,
        relationship_evidence={"comfort": 4},
        user_name="Profile User",
    )
    global_full_id = store.add_memory_item(
        Scope(2, 30, 100, True),
        "globally disclosed relationship",
        kind="relationship",
        disclosure="global",
        confidence=0.7,
        relationship_evidence={"support_openness": 1},
        user_name="Profile User",
    )
    store.close()

    service = MemoryService(AdminRepository(database))

    data = service.relationship_profiles(
        target_guild_id="1",
        target_channel_id="99",
        query="Profile",
    )

    assert data["target"] == {
        "scope_type": "guild",
        "guild_id": 1,
        "channel_id": 99,
        "guild_name": "Profile Guild",
        "channel_name": "target",
    }
    assert data["axes"] == (
        "familiarity",
        "comfort",
        "casualness",
        "teasing_tolerance",
        "support_openness",
        "task_orientation",
    )
    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["user_id"] == "100"
    assert row["observation_count"] == 5
    assert row["profile"] == {"familiarity": 3, "comfort": 3}
    assert row["full_relationship_count"] == 2
    assert [item["id"] for item in row["full_relationships"]] == [
        same_space_id,
        global_full_id,
    ]
    assert row["full_relationships"][0]["content"] == (
        "same disclosure space relationship"
    )
    assert row["full_relationships"][0]["guild_name"] == "Profile Guild"
    assert row["full_relationships"][0]["channel_name"] == "profile"
    assert row["full_relationships"][1]["disclosure"] == "global"
    assert row["used_observations"] == 3
    assert [item["id"] for item in row["contributors"]] == [
        low_conf_id,
        second_id,
        first_id,
    ]
    assert [item["projected_evidence"] for item in row["contributors"]] == [
        {"comfort": 4},
        {"familiarity": 2},
        {"familiarity": 2},
    ]
    assert [item["axis_ages"] for item in row["contributors"]] == [
        {"comfort": 0},
        {"familiarity": 0},
        {"familiarity": 1},
    ]

    dm_target = service.relationship_profiles(
        target_scope_type="dm",
        target_guild_id="999",
        target_channel_id="10",
        query="Profile",
    )
    assert dm_target["target"] == {
        "scope_type": "dm",
        "guild_id": None,
        "channel_id": None,
        "guild_name": "",
        "channel_name": "",
    }
    assert dm_target["filters"]["target_guild_id"] == ""
    assert dm_target["filters"]["target_channel_id"] == ""
    dm_row = dm_target["rows"][0]
    assert dm_row["profile"] == {
        "familiarity": 3,
        "comfort": 3,
        "casualness": 4,
        "support_openness": 1,
    }
    assert dm_row["used_observations"] == 5
    assert [item["id"] for item in dm_row["contributors"]] == [
        global_full_id,
        low_conf_id,
        same_space_id,
        second_id,
        first_id,
    ]
    assert dm_row["full_relationship_count"] == 5
    assert {item["id"] for item in dm_row["full_relationships"]} == {
        first_id,
        second_id,
        same_space_id,
        low_conf_id,
        global_full_id,
    }

    dm_without_channel = service.relationship_profiles(
        target_scope_type="dm",
        query="Profile",
    )
    assert dm_without_channel["target"] == {
        "scope_type": "dm",
        "guild_id": None,
        "channel_id": None,
        "guild_name": "",
        "channel_name": "",
    }
    assert dm_without_channel["target_error"] == ""

    no_target = service.relationship_profiles(query="Profile")
    assert no_target["target"] is None
    assert no_target["rows"][0]["profile"] == {}
    assert no_target["rows"][0]["full_relationships"] == []
    assert no_target["rows"][0]["full_relationship_count"] == 0
    assert no_target["rows"][0]["used_observations"] == 0



def test_relationship_profiles_include_users_with_only_full_relationships(tmp_path):
    database = tmp_path / "full-only-relationships.sqlite3"
    store = Store(str(database))
    store.add_memory_item(
        Scope(2, 30, 200, True),
        "globally visible relationship only",
        kind="relationship",
        disclosure="global",
        confidence=0.6,
        user_name="Full Only User",
    )
    store.close()

    service = MemoryService(AdminRepository(database))

    data = service.relationship_profiles(
        target_guild_id="1",
        target_channel_id="99",
        query="Full Only",
    )

    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["user_id"] == "200"
    assert row["observation_count"] == 1
    assert row["profile"] == {}
    assert row["used_observations"] == 0
    assert row["full_relationship_count"] == 1
    assert row["full_relationships"][0]["content"] == (
        "globally visible relationship only"
    )


def test_context_state_resolves_modes_capture_and_manual_notes(tmp_path):
    database = tmp_path / "context-state.sqlite3"
    store = Store(str(database))
    guild = Scope(1, 10, 100)
    dm = Scope(None, 20, 100)
    store.observe_guild_channel(1, "State Guild", 10, "state")

    store.add(guild, 1, "state source", "reply", name="State User")
    store.set_memory_mode_override("global", "off")
    store.set_memory_mode_override(guild.realm, "read_only")
    store.set_memory_mode_override(dm.channel, "read_only")
    store.set_chat_log_mode_override("global", "on")
    store.set_chat_log_mode_override(guild.realm, "off")
    store.set_note("config:chatlog_capture:global", "direct")
    store.set_note("config:chatlog_unified_v1", "1")
    store.set_note(guild.realm, "guild-wide manual note")
    store.set_note(guild.user_note, "guild user manual note")
    store.set_note(dm.user_note, "dm user manual note")
    store.close()

    service = ContextStateService(AdminRepository(database))

    data = service.context_state(
        target_guild_id="1",
        target_channel_id="10",
        target_user_id="100",
    )
    effective = data["effective"]
    assert effective is not None
    assert effective["user_name"] == "State User"
    assert effective["guild_name"] == "State Guild"
    assert effective["channel_name"] == "state"
    assert effective["memory"]["chain"]["effective"] == "read_only"
    assert effective["memory"]["chain"]["source"] == "server"
    assert effective["memory"]["reads"] is True
    assert effective["memory"]["writes"] is False
    assert effective["chat_log"]["enabled_chain"]["effective"] == "off"
    assert effective["chat_log"]["capture_chain"]["effective"] == "direct"
    assert effective["chat_log"]["effective"] == "off"
    assert effective["notes"]["server"] == "guild-wide manual note"
    assert effective["notes"]["user"] == "guild user manual note"

    assert {(row["scope"], row["mode"]) for row in data["memory_overrides"]} == {
        ("global", "off"),
        ("guild:1", "read_only"),
        ("dm:100:channel:20", "read_only"),
    }
    assert [
        {key: row[key] for key in ("scope", "enabled", "capture")}
        for row in data["chat_overrides"]
    ] == [
        {"scope": "global", "enabled": "on", "capture": "direct"},
        {"scope": "guild:1", "enabled": "off", "capture": None},
    ]
    guild_chat_override = next(
        row for row in data["chat_overrides"] if row["scope"] == "guild:1"
    )
    assert guild_chat_override["guild_name"] == "State Guild"
    assert guild_chat_override["channel_name"] == ""
    assert data["internal_note_count"] == 2
    assert {row["text"] for row in data["manual_notes"]} == {
        "guild-wide manual note",
        "guild user manual note",
        "dm user manual note",
    }
    user_note = next(
        row for row in data["manual_notes"] if row["text"] == "guild user manual note"
    )
    assert user_note["user_name"] == "State User"

    filtered = service.context_state(query="State User")
    assert [row["text"] for row in filtered["manual_notes"]] == [
        "guild user manual note"
    ]

    dm_data = service.context_state(
        target_channel_id="20",
        target_user_id="100",
    )
    dm_effective = dm_data["effective"]
    assert dm_effective is not None
    assert dm_effective["memory"]["chain"]["effective"] == "read_only"
    assert dm_effective["memory"]["chain"]["source"] == "channel"
    assert dm_effective["chat_log"]["effective"] == "off"
    assert dm_effective["chat_log"]["guild_recent_context_applicable"] is False
    assert dm_effective["notes"]["server"] == ""
    assert dm_effective["notes"]["user"] == "dm user manual note"
    assert dm_data["filters"]["target_scope_type"] == "dm"

    explicit_dm = service.context_state(
        target_scope_type="dm",
        target_guild_id="999",
        target_channel_id="20",
        target_user_id="100",
    )
    assert explicit_dm["effective"] is not None
    assert explicit_dm["effective"]["scope"].guild_id is None
    assert explicit_dm["filters"]["target_guild_id"] == ""

    auto_dm = service.context_state(
        target_scope_type="dm",
        target_user_id="100",
    )
    assert auto_dm["effective"] is not None
    assert auto_dm["effective"]["scope"].channel_id == 20
    assert auto_dm["dm_channel_selection"]["candidates"] == ["20"]
    assert auto_dm["dm_channel_selection"]["selected"] == "20"
    assert auto_dm["dm_channel_selection"]["auto_selected"] is True


def test_context_state_requires_selection_for_multiple_dm_channels(tmp_path):
    database = tmp_path / "context-state-multi-dm.sqlite3"
    store = Store(str(database))
    first = Scope(None, 20, 100)
    second = Scope(None, 21, 100)
    store.add(first, 1, "first dm", "reply", name="State User")
    store.add(second, 2, "second dm", "reply", name="State User")
    store.set_memory_mode_override(second.channel, "write_only")
    store.close()

    service = ContextStateService(AdminRepository(database))

    unresolved = service.context_state(
        target_scope_type="dm",
        target_user_id="100",
    )
    assert unresolved["effective"] is None
    assert unresolved["target_error"] == ""
    assert unresolved["dm_channel_selection"]["candidates"] == ["20", "21"]
    assert unresolved["dm_channel_selection"]["selection_required"] is True

    selected = service.context_state(
        target_scope_type="dm",
        target_dm_channel_id="21",
        target_user_id="100",
    )
    assert selected["effective"] is not None
    assert selected["effective"]["scope"].channel_id == 21
    assert selected["effective"]["memory"]["chain"]["effective"] == "write_only"
    assert selected["effective"]["memory"]["chain"]["source"] == "channel"

    unknown = service.context_state(
        target_scope_type="dm",
        target_user_id="999",
    )
    assert unknown["effective"] is None
    assert unknown["dm_channel_selection"]["unavailable"] is True

    manual = service.context_state(
        target_scope_type="dm",
        target_dm_channel_id="77",
        target_user_id="999",
    )
    assert manual["effective"] is not None
    assert manual["effective"]["scope"].channel_id == 77


def test_context_state_rejects_partial_or_invalid_target(tmp_path):
    memory_service, _, _ = build_memory_service(tmp_path)
    service = ContextStateService(memory_service.repository)

    partial = service.context_state(target_guild_id="1", target_user_id="100")
    assert partial["effective"] is None
    assert "Channel ID와 User ID를 입력하세요" in partial["target_error"]

    missing_guild = service.context_state(
        target_scope_type="guild",
        target_channel_id="10",
        target_user_id="100",
    )
    assert missing_guild["effective"] is None
    assert "Guild ID가 필요합니다" in missing_guild["target_error"]

    invalid = service.context_state(
        target_guild_id="-1",
        target_channel_id="10",
        target_user_id="100",
    )
    assert invalid["effective"] is None
    assert "양의 정수" in invalid["target_error"]

    invalid_dm_user = service.context_state(
        target_scope_type="dm",
        target_user_id="nope",
    )
    assert invalid_dm_user["effective"] is None
    assert "양의 정수" in invalid_dm_user["target_error"]



def test_telemetry_views_default_to_current_observability_epoch(tmp_path):
    database = tmp_path / "epochs.sqlite3"
    store = Store(str(database))
    with store.db:
        store.db.execute(
            """CREATE TABLE observability_epochs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   reset_at TEXT NOT NULL
               )"""
        )
        store.db.execute(
            "INSERT INTO observability_epochs(reset_at) VALUES (?)",
            ("2026-09-15T00:00:00+00:00",),
        )
    store.close()

    usage = tmp_path / "logs" / "usage.jsonl"
    events = tmp_path / "logs" / "events.jsonl"
    write_rows(
        usage,
        [
            {
                "at": "2026-09-10T00:00:01+00:00",
                "turn_id": "old-turn",
                "operation": "answer",
                "model": "old-model",
                "model_tier": "fast",
                "status": "completed",
            },
            {
                "at": "2026-09-20T00:00:01+00:00",
                "turn_id": "new-turn",
                "operation": "answer",
                "model": "new-model",
                "model_tier": "smart",
                "status": "completed",
            },
        ],
    )
    write_rows(
        events,
        [
            {
                "at": "2026-09-10T00:00:00+00:00",
                "turn_id": "old-turn",
                "event": "turn.completed",
                "scope": "guild",
                "status": "completed",
            },
            {
                "at": "2026-09-20T00:00:00+00:00",
                "turn_id": "new-turn",
                "event": "turn.completed",
                "scope": "dm",
                "status": "completed",
            },
        ],
    )

    repository = AdminRepository(database)
    telemetry = TelemetryReader(usage, events)
    trace_service = TraceService(repository, telemetry)

    current = select_observability_epoch(
        telemetry.snapshot(),
        repository.observability_epochs(),
        "",
    )
    analytics = build_analytics(current.snapshot)
    assert current.selected == "1"
    assert current.selected == current.current
    assert analytics["usage"]["api_calls"] == 1
    assert analytics["usage"]["models"][0]["name"] == "new-model"

    all_selection = select_observability_epoch(
        telemetry.snapshot(),
        repository.observability_epochs(),
        "all",
    )
    all_analytics = build_analytics(all_selection.snapshot)
    assert all_analytics["usage"]["api_calls"] == 2
    assert all_selection.selected == "all"

    traces = trace_service.traces()
    assert traces["page"].total == 1
    assert traces["rows"][0]["turn_id"] == "new-turn"
    assert trace_service.traces(epoch="all")["page"].total == 2

    overview = trace_service.overview()
    assert overview["epoch"]["selected"] == "1"
    assert overview["trace_count"] == 1


def test_overview_distinguishes_missing_partial_and_observed_zero(tmp_path):
    service = build_service(tmp_path)
    path = tmp_path / "logs" / "discord-usage.jsonl"
    write_rows(path, [
        {"turn_id": "trace-1", "at": "2026-09-21T00:00:01+00:00", "calls": 0,
         "total_tokens": 0},
        {"turn_id": "trace-2", "at": "2026-09-21T00:01:01+00:00"},
    ])
    data = service.overview()
    assert data["metrics"]["total_tokens"]["value"] == 0
    assert data["metrics"]["total_tokens"]["partial"] is True
    assert data["metrics"]["input_tokens"]["value"] is None
    path.unlink()
    data = service.overview()
    assert data["api_calls"] is None
    assert data["missing_exchanges"] > 0


def test_memory_failure_filter_uses_count_not_search_text(tmp_path):
    service = build_service(tmp_path)
    overview = service.overview()
    data = service.traces(memory_failure="yes")
    assert data["page"].total == overview["memory_failure_traces"] == 1
    assert data["rows"][0]["memory_failures"] == overview["memory_failures"]
    assert service.traces(query="memory")["page"].total == 0
    assert service.traces(memory_failure="no")["page"].total == 1
