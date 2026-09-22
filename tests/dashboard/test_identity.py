from hina_bot.dashboard.identity import build_identity_observability
from hina_bot.dashboard.telemetry import TelemetrySnapshot


def snapshot():
    events = (
        {
            "at": "2026-09-22T00:00:00+00:00",
            "event": "identity.resolution",
            "turn_id": "t1",
            "outcome": "resolved",
            "resolver_invoked": True,
            "candidate_count": 5,
            "raw_candidate_count": 7,
            "reference_group": "ref-a",
            "resolved_user_group": "user-1",
            "evidence_source": "resolver_derived",
        },
        {
            "at": "2026-09-22T00:01:00+00:00",
            "event": "identity.resolution",
            "turn_id": "t2",
            "outcome": "resolved",
            "resolver_invoked": True,
            "candidate_count": 5,
            "raw_candidate_count": 7,
            "reference_group": "ref-a",
            "resolved_user_group": "user-1",
            "evidence_source": "resolver_derived",
        },
        {
            "at": "2026-09-22T00:02:00+00:00",
            "event": "identity.resolution",
            "turn_id": "t3",
            "outcome": "ambiguous",
            "resolver_invoked": True,
            "candidate_count": 18,
            "raw_candidate_count": 18,
            "reference_group": "ref-b",
            "evidence_source": "resolver_derived",
        },
        {
            "at": "2026-09-22T00:03:00+00:00",
            "event": "identity.resolution",
            "turn_id": "t4",
            "outcome": "blocked",
            "resolver_invoked": False,
            "candidate_count": 0,
            "raw_candidate_count": 4,
            "blocked_reason": "no_visible_candidates",
            "evidence_source": "policy",
        },
        {
            "at": "2026-09-22T00:04:00+00:00",
            "event": "identity.resolution",
            "turn_id": "t5",
            "outcome": "resolved",
            "resolver_invoked": True,
            "candidate_count": 3,
            "raw_candidate_count": 3,
            "reference_group": "ref-c",
            "resolved_user_group": "user-1",
            "evidence_source": "resolver_derived",
        },
        {
            "at": "2026-09-22T00:05:00+00:00",
            "event": "identity.resolution",
            "turn_id": "t6",
            "outcome": "resolved",
            "resolver_invoked": True,
            "candidate_count": 3,
            "raw_candidate_count": 3,
            "reference_group": "ref-c",
            "resolved_user_group": "user-2",
            "evidence_source": "resolver_derived",
        },
    )
    usage = (
        {
            "at": "2026-09-22T00:00:00+00:00",
            "operation": "identity_resolve",
            "turn_id": "t1",
            "model": "identity-model",
            "provider": "gemini",
            "status": "completed",
            "total_tokens": 40,
            "elapsed_ms": 180,
        },
        {
            "at": "2026-09-22T00:01:00+00:00",
            "operation": "identity_resolve",
            "turn_id": "t2",
            "model": "identity-model",
            "provider": "gemini",
            "status": "completed",
            "elapsed_ms": 220,
        },
    )
    return TelemetrySnapshot(
        usage=usage,
        exchanges=(),
        events=events,
        oldest_at="2026-09-22T00:00:00+00:00",
        newest_at="2026-09-22T00:05:00+00:00",
    )


def test_identity_observability_summarizes_outcomes_and_usage():
    data = build_identity_observability(snapshot())

    assert data["summary"]["invoked"] == 5
    assert data["summary"]["resolved"] == 4
    assert data["summary"]["ambiguous"] == 1
    assert data["summary"]["blocked"] == 1
    assert data["summary"]["potential_cache_hits"] == 1
    assert data["summary"]["potential_hit_rate"] == 20.0
    assert data["summary"]["conflicting_groups"] == 1
    assert data["summary"]["assistant_generated_evidence"] == 0
    assert data["blocked_reasons"] == {"no_visible_candidates": 1}
    assert data["usage"]["calls"] == 2
    assert data["usage"]["total_tokens"] == 40
    assert data["usage"]["token_known"] == 1
    assert data["usage"]["token_missing"] == 1
    assert data["usage"]["latency_p50"] == 180
    assert data["usage"]["latency_p95"] == 220


def test_identity_repeat_groups_are_privacy_safe_and_conflict_aware():
    data = build_identity_observability(snapshot())

    groups = {row["reference_group"]: row for row in data["repeat_groups"]}
    assert groups["ref-a"]["potential_hits"] == 1
    assert groups["ref-a"]["stable"] is True
    assert groups["ref-c"]["conflicts"] == 1
    assert groups["ref-c"]["distinct_users"] == 2
    assert groups["ref-c"]["stable"] is False


def test_identity_filters_recent_cases_without_changing_summary_window():
    data = build_identity_observability(
        snapshot(),
        outcome="blocked",
        blocked_reason="no_visible_candidates",
        after="2026-09-22T00:02:30+00:00",
        before="2026-09-22T00:04:30+00:00",
    )

    assert data["summary"]["events"] == 2
    assert len(data["recent"]) == 1
    assert data["recent"][0]["turn_id"] == "t4"
