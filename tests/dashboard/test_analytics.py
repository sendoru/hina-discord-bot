from hina_bot.dashboard.analytics import build_analytics
from hina_bot.dashboard.telemetry import TelemetrySnapshot


def snapshot():
    usage = (
        {
            "at": "2026-09-21T00:00:00+00:00",
            "turn_id": "a",
            "operation": "answer",
            "provider": "gemini",
            "model": "smart-model",
            "status": "completed",
            "model_tier": "smart",
            "model_route_baseline_tier": "fast",
            "model_route_score": 2.1,
            "model_route_threshold": 2.0,
            "model_route_margin": 0.1,
            "model_route_policy": "chat-hybrid-v4",
            "model_route_decision_source": "semantic",
            "model_route_components": {
                "request_load": 0.4,
                "context_load": 0.5,
                "semantic_score": 1.0,
            },
            "model_route_reasons": ["request_load", "semantic_score"],
            "semantic_route_status": "completed",
            "semantic_route_level": "medium",
            "search_route_baseline_mode": "none",
            "search_route_mode": "required",
            "search_route_decision_source": "semantic",
            "search_route_locked": False,
            "semantic_web_need": "required",
            "semantic_web_uncertain": False,
            "web_search_used": True,
            "web_search_calls": 1,
            "input_tokens": 100,
            "output_tokens": 30,
            "total_tokens": 130,
            "cached_tokens": 10,
            "reasoning_tokens": 20,
            "elapsed_ms": 900,
        },
        {
            "at": "2026-09-21T00:00:01+00:00",
            "turn_id": "a",
            "operation": "model_route_classify",
            "provider": "gemini",
            "model": "classifier",
            "status": "completed",
            "total_tokens": 40,
            "elapsed_ms": 200,
        },
        {
            "at": "2026-09-21T00:01:00+00:00",
            "turn_id": "b",
            "operation": "answer",
            "provider": "gemini",
            "model": "fast-model",
            "status": "completed",
            "model_tier": "fast",
            "model_route_baseline_tier": "fast",
            "model_route_margin": -0.8,
            "model_route_components": {
                "request_load": 0.2,
                "context_load": 0.1,
                "semantic_score": 0.0,
            },
            "model_route_reasons": ["request_load"],
            "semantic_route_status": "not_used",
            "search_route_baseline_mode": "auto",
            "search_route_mode": "auto",
            "search_route_decision_source": "deterministic",
            "search_route_locked": False,
            "semantic_web_need": "",
            "web_search_used": False,
            "web_search_calls": 0,
            "input_tokens": 60,
            "output_tokens": 10,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "elapsed_ms": 300,
        },
        {
            "at": "2026-09-21T00:02:00+00:00",
            "turn_id": "c",
            "operation": "summarize",
            "provider": "openai",
            "model": "memory-model",
            "status": "error",
            "elapsed_ms": 500,
        },
        {
            "at": "2026-09-21T00:01:01+00:00",
            "turn_id": "a",
            "operation": "context.size",
            "status": "completed",
            "context_chars_total": 1000,
            "context_summary_chars": 100,
            "context_structured_memory_chars": 200,
            "context_recent_chars": 150,
            "context_public_chars": 50,
            "context_channel_chars": 120,
            "context_reply_chars": 80,
            "context_history_chars": 60,
            "context_lore_chars": 90,
            "context_emoji_chars": 20,
            "instruction_chars": 1400,
            "visible_input_chars": 30,
        },
        {
            "at": "2026-09-21T00:03:00+00:00",
            "turn_id": "d",
            "operation": "model_route_shadow",
            "status": "completed",
            "model_route_baseline_tier": "fast",
            "model_route_proposed_tier": "smart",
            "semantic_route_level": "high",
            "routing_classifier_provider": "gemini",
            "search_route_baseline_mode": "none",
            "search_route_proposed_mode": "required",
            "semantic_web_need": "required",
        },
    )
    exchanges = (
        {
            "at": "2026-09-21T00:00:02+00:00",
            "turn_id": "a",
            "calls": 2,
            "total_tokens": 170,
            "usage_complete": True,
            "elapsed_ms": 1200,
        },
        {
            "at": "2026-09-21T00:01:02+00:00",
            "turn_id": "b",
            "calls": 1,
            "usage_complete": False,
            "elapsed_ms": 400,
        },
    )
    events = (
        {
            "at": "2026-09-21T00:00:00+00:00",
            "turn_id": "a",
            "event": "turn.preflight",
            "preflight_ms": 100,
            "identity_ms": 20,
            "target_context_ms": 10,
            "reply_context_ms": 15,
            "visual_context_ms": 40,
        },
        {
            "at": "2026-09-21T00:00:02+00:00",
            "turn_id": "a",
            "event": "turn.reply_delivered",
            "elapsed_ms": 1500,
            "delivery_ms": 50,
            "delivery_chunks": 1,
        },
        {
            "at": "2026-09-21T00:00:03+00:00",
            "turn_id": "a",
            "event": "turn.completed",
            "elapsed_ms": 1800,
            "lock_wait_ms": 10,
            "slot_wait_ms": 20,
            "recent_history_ms": 30,
            "context_ms": 100,
            "generation_ms": 1200,
            "delivery_ms": 50,
            "memory_ms": 280,
        },
        {
            "at": "2026-09-21T00:01:00+00:00",
            "turn_id": "b",
            "event": "turn.preflight",
            "preflight_ms": 50,
            "identity_ms": 1,
            "target_context_ms": 4,
            "reply_context_ms": 5,
            "visual_context_ms": 10,
        },
        {
            "at": "2026-09-21T00:01:01+00:00",
            "turn_id": "b",
            "event": "turn.reply_delivered",
            "elapsed_ms": 400,
            "delivery_ms": 20,
            "delivery_chunks": 1,
        },
        {
            "at": "2026-09-21T00:01:02+00:00",
            "turn_id": "b",
            "event": "turn.completed",
            "elapsed_ms": 450,
            "lock_wait_ms": 5,
            "slot_wait_ms": 10,
            "context_ms": 50,
            "generation_ms": 350,
            "delivery_ms": 20,
        },
    )
    return TelemetrySnapshot(
        usage=usage,
        exchanges=exchanges,
        events=events,
        oldest_at="2026-09-21T00:00:00+00:00",
        newest_at="2026-09-21T00:03:00+00:00",
    )


def test_analytics_preserves_missing_usage_and_breaks_down_calls():
    data = build_analytics(snapshot())

    assert data["usage"]["api_calls"] == 4
    assert data["usage"]["tokens"]["total_tokens"] == {
        "value": 170,
        "known": 2,
        "missing": 2,
    }
    assert data["usage"]["memory_calls"] == 1
    assert data["usage"]["memory_tokens"]["known"] == 0
    assert data["usage"]["errors"] == 1
    assert data["exchanges"]["usage_completeness"] == {
        "complete": 1,
        "partial": 1,
    }
    by_operation = {row["name"]: row for row in data["usage"]["operations"]}
    assert by_operation["answer"]["calls"] == 2
    assert by_operation["answer"]["total_tokens"]["known"] == 1
    assert by_operation["summarize"]["errors"] == 1


def test_analytics_exposes_model_routing_classifier_and_shadow_deltas():
    data = build_analytics(snapshot())

    assert data["routing"]["tiers"] == {"fast": 1, "smart": 1}
    assert data["routing"]["baseline_to_final"]["rows"] == [
        {"from": "fast", "to": "fast", "count": 1},
        {"from": "fast", "to": "smart", "count": 1},
    ]
    assert data["routing"]["near_threshold"] == 1
    assert data["routing"]["known_margins"] == 2
    assert data["routing"]["classifier"]["calls"] == 1
    assert data["routing"]["classifier"]["tokens"]["value"] == 40
    assert data["routing"]["shadow"]["tier_transitions"]["rows"] == [
        {"from": "fast", "to": "smart", "count": 1}
    ]
    assert data["routing"]["shadow"]["search_transitions"]["rows"] == [
        {"from": "none", "to": "required", "count": 1}
    ]


def test_analytics_exposes_search_mode_vs_actual_tool_use():
    data = build_analytics(snapshot())

    assert data["search"]["outcomes"] == {
        "auto → unused": 1,
        "required → used": 1,
    }
    assert data["search"]["decision_source"] == {
        "deterministic": 1,
        "semantic": 1,
    }
    assert data["search"]["semantic_web_need"] == {"required": 1}


def test_analytics_filters_per_call_usage_but_keeps_routing_answer_scope():
    data = build_analytics(
        snapshot(),
        operation="model_route_classify",
        provider="gemini",
        after="2026-09-20T23:59:00+00:00",
        before="2026-09-21T00:01:30+00:00",
    )

    assert data["usage"]["api_calls"] == 1
    assert data["usage"]["operations"][0]["name"] == "model_route_classify"
    # Operation filtering is intentionally usage-only; routing still evaluates answer rows.
    assert data["routing"]["answer_rows"] == 2
    assert data["routing"]["classifier"]["calls"] == 1


def test_analytics_exposes_user_visible_latency_and_generation_residual():
    data = build_analytics(snapshot())

    assert data["performance"]["reply_latency"] == {
        "known": 2,
        "missing": 0,
        "average": 1025,
        "p50": 450,
        "p95": 1600,
        "max": 1600,
    }
    assert data["performance"]["turn_latency"]["p95"] == 1900
    assert data["performance"]["post_reply"]["average"] == 175
    assert data["performance"]["stages"]["recent_history_ms"]["known"] == 1
    assert data["performance"]["stages"]["recent_history_ms"]["missing"] == 1
    assert data["performance"]["generation"]["api_time"]["average"] == 700
    assert data["performance"]["generation"]["residual"]["average"] == 75


def test_analytics_exposes_api_categories_and_context_char_attribution():
    data = build_analytics(snapshot())

    assert data["usage"]["memory_calls"] == 1
    assert data["performance"]["api_categories"]["answer"]["calls"] == 2
    assert data["performance"]["api_categories"]["routing"]["calls"] == 1
    assert data["performance"]["api_categories"]["memory"]["calls"] == 1
    assert data["performance"]["context_chars"]["context_chars_total"]["average"] == 1000
    assert data["performance"]["context_chars"]["instruction_chars"]["average"] == 1400
