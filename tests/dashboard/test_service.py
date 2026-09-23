import json

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.repository import AdminRepository
from hina_bot.dashboard.service import DashboardService
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
            },
        ],
    )
    return DashboardService(
        AdminRepository(database),
        TelemetryReader(usage, events),
    )


def test_overview_aggregates_trace_and_exchange_metrics(tmp_path):
    service = build_service(tmp_path)

    data = service.overview()

    assert data["trace_count"] == 2
    assert data["stored_turn_count"] == 1
    assert data["status_counts"] == {"generation_failed": 1, "completed": 1}
    assert data["tier_counts"] == {"fast": 1, "smart": 1}
    assert data["api_calls"] == 2
    assert data["tokens"]["total_tokens"] == 190
    assert data["web_search_calls"] == 1
    assert data["memory_failures"] == 1
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


def test_trace_detail_correlates_raw_turn_and_timeline(tmp_path):
    service = build_service(tmp_path)

    data = service.trace("trace-1")

    assert data is not None
    assert data["stored"]["content"] == "question"
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
    assert [item["source"] for item in data["timeline"]] == [
        "event",
        "usage",
        "exchange",
        "event",
    ]
    assert service.trace("missing") is None


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


def test_conversations_use_bounded_repository_filters(tmp_path):
    service = build_service(tmp_path)

    data = service.conversations(query="question")

    assert data["page"].total == 1
    assert data["rows"][0]["reply"] == "answer"
    assert service.conversations(query="not-found")["page"].total == 0


def build_memory_service(tmp_path):
    database = tmp_path / "memory.sqlite3"
    store = Store(str(database))
    scope = Scope(1, 10, 100, True)
    token = CURRENT_TURN_ID.set("memory-trace")
    try:
        store.add(scope, 700, "source for memory", "reply")
    finally:
        CURRENT_TURN_ID.reset(token)
    source_turn_id = int(store.db.execute(
        "SELECT id FROM turns WHERE message_id='700'"
    ).fetchone()["id"])
    store.save_summary(scope, "legacy personal summary", source_turn_id)
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
        DashboardService(
            AdminRepository(database),
            TelemetryReader("", ""),
        ),
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

    detail = service.memory_item(item_id)
    assert detail is not None
    assert detail["item"]["content"] == "remembered fact"
    assert detail["sources"][0]["turn"]["turn_id"] == "memory-trace"


def test_summary_and_cursor_service_show_rollout_state(tmp_path):
    service, _, scope = build_memory_service(tmp_path)

    summaries = service.summaries(user_id="100")
    assert len(summaries["personal"]) == 1
    assert summaries["personal"][0]["structured_memory_count"] == 1

    cursors = service.extraction_cursors(user_id="100")
    row = next(row for row in cursors["rows"] if row["scope"] == scope.conversation)
    assert row["initialized"] == 0
    assert row["effective_through_id"] == row["summary_through_id"]
    assert row["cursor_delta"] is None


def build_reconciliation_service(tmp_path):
    database = tmp_path / "reconciliation.sqlite3"
    store = Store(str(database))
    scope = Scope(None, 10, 100)

    token = CURRENT_TURN_ID.set("trace-old")
    try:
        store.add(scope, 101, "old source", "reply")
    finally:
        CURRENT_TURN_ID.reset(token)
    token = CURRENT_TURN_ID.set("trace-extract")
    try:
        store.add(scope, 102, "new correction source", "reply")
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
        DashboardService(AdminRepository(database), TelemetryReader(usage, events)),
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
    assert listing["rows"][0]["same_source_set"] is True
    assert listing["rows"][0]["source_overlap_ids"] == ("101", "102")

    detail = service.reconciliation_proposal(proposal_id)

    assert detail is not None
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
    service.traces_service.timezone = "Asia/Seoul"

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



def test_relationship_profiles_match_runtime_cross_space_projection(tmp_path):
    database = tmp_path / "relationships.sqlite3"
    store = Store(str(database))
    dm = Scope(None, 10, 100)
    same_target_guild = Scope(1, 20, 100, True)

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
    store.add_memory_item(
        same_target_guild,
        "same disclosure space relationship",
        kind="relationship",
        disclosure="implicit",
        confidence=0.99,
        relationship_evidence={"casualness": 4},
        user_name="Profile User",
    )
    store.add_memory_item(
        dm,
        "low confidence relationship",
        kind="relationship",
        disclosure="implicit",
        confidence=0.79,
        relationship_evidence={"comfort": 4},
        user_name="Profile User",
    )
    store.close()

    service = DashboardService(
        AdminRepository(database),
        TelemetryReader("", ""),
    )

    data = service.relationship_profiles(
        target_guild_id="1",
        target_channel_id="99",
        query="Profile",
    )

    assert data["target"] == {"guild_id": 1, "channel_id": 99}
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
    assert row["observation_count"] == 4
    assert row["profile"] == {"familiarity": 3}
    assert row["used_observations"] == 2
    assert [item["id"] for item in row["contributors"]] == [second_id, first_id]
    assert all(item["evidence"] == {"familiarity": 2} for item in row["contributors"])

    no_target = service.relationship_profiles(query="Profile")
    assert no_target["target"] is None
    assert no_target["rows"][0]["profile"] == {}
    assert no_target["rows"][0]["used_observations"] == 0



def test_context_state_resolves_modes_capture_and_manual_notes(tmp_path):
    database = tmp_path / "context-state.sqlite3"
    store = Store(str(database))
    guild = Scope(1, 10, 100)
    dm = Scope(None, 20, 100)

    store.add(guild, 1, "state source", "reply", name="State User")
    store.set_memory_mode_override("global", "off")
    store.set_memory_mode_override(guild.realm, "read_only")
    store.set_chat_log_mode_override("global", "on")
    store.set_chat_log_mode_override(guild.realm, "off")
    store.set_note("config:chatlog_capture:global", "direct")
    store.set_note("config:chatlog_unified_v1", "1")
    store.set_note(guild.realm, "guild-wide manual note")
    store.set_note(guild.user_note, "guild user manual note")
    store.set_note(dm.user_note, "dm user manual note")
    store.close()

    service = DashboardService(
        AdminRepository(database),
        TelemetryReader("", ""),
    )

    data = service.context_state(
        target_guild_id="1",
        target_channel_id="10",
        target_user_id="100",
    )
    effective = data["effective"]
    assert effective is not None
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
    }
    assert data["chat_overrides"] == [
        {"scope": "global", "enabled": "on", "capture": "direct"},
        {"scope": "guild:1", "enabled": "off", "capture": None},
    ]
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
    assert dm_effective["memory"]["chain"]["effective"] == "off"
    assert dm_effective["chat_log"]["effective"] == "off"
    assert dm_effective["chat_log"]["guild_recent_context_applicable"] is False
    assert dm_effective["notes"]["server"] == ""
    assert dm_effective["notes"]["user"] == "dm user manual note"


def test_context_state_rejects_partial_or_invalid_target(tmp_path):
    service, _, _ = build_memory_service(tmp_path)

    partial = service.context_state(target_guild_id="1", target_user_id="100")
    assert partial["effective"] is None
    assert "Channel ID and user ID are required" in partial["target_error"]

    invalid = service.context_state(
        target_guild_id="-1",
        target_channel_id="10",
        target_user_id="100",
    )
    assert invalid["effective"] is None
    assert "positive integers" in invalid["target_error"]



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
                "at": "2026-09-10T00:00:00+00:00",
                "turn_id": "old-turn",
                "event": "identity.resolution",
                "outcome": "resolved",
                "resolver_invoked": True,
            },
            {
                "at": "2026-09-20T00:00:00+00:00",
                "turn_id": "new-turn",
                "event": "turn.completed",
                "scope": "dm",
                "status": "completed",
            },
            {
                "at": "2026-09-20T00:00:00+00:00",
                "turn_id": "new-turn",
                "event": "identity.resolution",
                "outcome": "resolved",
                "resolver_invoked": True,
            },
        ],
    )

    service = DashboardService(
        AdminRepository(database),
        TelemetryReader(usage, events),
    )

    analytics = service.analytics()
    assert analytics["epoch"]["selected"] == "1"
    assert analytics["epoch"]["is_current"] is True
    assert analytics["usage"]["api_calls"] == 1
    assert analytics["usage"]["models"][0]["name"] == "new-model"

    all_analytics = service.analytics(epoch="all")
    assert all_analytics["usage"]["api_calls"] == 2
    assert all_analytics["epoch"]["selected"] == "all"

    traces = service.traces()
    assert traces["page"].total == 1
    assert traces["rows"][0]["turn_id"] == "new-turn"
    assert service.traces(epoch="all")["page"].total == 2

    identity = service.identity_observability()
    assert identity["epoch"]["selected"] == "1"
    assert identity["summary"]["resolved"] == 1
    assert service.identity_observability(epoch="all")["summary"]["resolved"] == 2

    overview = service.overview()
    assert overview["epoch"]["selected"] == "1"
    assert overview["trace_count"] == 1
